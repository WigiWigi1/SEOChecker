import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, abort

from config import CONFIG
from seo.crawler import MiniCrawler
from seo.runner import run_checks
from seo.output import make_audit_skeleton


app = Flask(__name__)
# IMPORTANT: обязательно задай в env на NAS
# export FLASK_SECRET_KEY="..."
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-change-me")

# DB + Pro KB paths
DB_PATH = os.environ.get("SEOCHECKER_DB_PATH", os.path.join(os.path.dirname(__file__), "seochecker.db"))
PRO_KB_DIR = os.environ.get("PRO_KB_DIR", os.path.join(os.path.dirname(__file__), "private", "pro_kb"))


# ---------------------------
# Helpers
# ---------------------------
def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def load_json(path: str) -> dict:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_entitlements (
              user_id INTEGER PRIMARY KEY,
              is_pro INTEGER NOT NULL DEFAULT 0,
              pro_until TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS reports (
              id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              target_url TEXT NOT NULL,
              created_at TEXT NOT NULL,
              report_json TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_or_create_user_id() -> int:
    """
    Minimal identity: a per-browser session user.
    Позже можно заменить на Google OAuth.
    """
    uid = session.get("user_id")
    if uid:
        return int(uid)

    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO users(created_at) VALUES (?)", (now_iso(),))
        user_id = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO user_entitlements(user_id, is_pro, pro_until) VALUES (?,?,?)",
            (user_id, 0, None)
        )
        conn.commit()
    finally:
        conn.close()

    session["user_id"] = int(user_id)
    return int(user_id)


def is_user_pro(user_id: int) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT is_pro, pro_until FROM user_entitlements WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return False

        is_pro_flag = int(row["is_pro"]) == 1
        if not is_pro_flag:
            return False

        # optional expiry
        pro_until = row["pro_until"]
        if not pro_until:
            return True

        # pro_until stored as ISO string
        try:
            dt = datetime.fromisoformat(pro_until.replace("Z", ""))
            return dt >= datetime.utcnow()
        except Exception:
            return True
    finally:
        conn.close()


def save_report(user_id: int, target_url: str, audit_json: dict) -> str:
    report_id = uuid.uuid4().hex[:12]
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO reports(id, user_id, target_url, created_at, report_json) VALUES (?,?,?,?,?)",
            (report_id, user_id, target_url, now_iso(), json.dumps(audit_json, ensure_ascii=False))
        )
        conn.commit()
        return report_id
    finally:
        conn.close()


def load_report(report_id: str, user_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT report_json FROM reports WHERE id = ? AND user_id = ?",
            (report_id, user_id)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["report_json"])
    finally:
        conn.close()


def safe_check_id(check_id: str) -> str | None:
    if not check_id:
        return None
    safe = "".join([c for c in check_id if c.isalnum() or c in ("_", "-")])
    if safe != check_id:
        return None
    return safe


def load_pro_fix_md(check_id: str) -> str | None:
    safe = safe_check_id(check_id)
    if not safe:
        return None

    md_path = os.path.join(PRO_KB_DIR, f"{safe}.md")
    if os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    return None


# init db on import
init_db()


# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def index():
    get_or_create_user_id()
    return render_template("index.html")


@app.post("/audit")
def audit():
    user_id = get_or_create_user_id()

    url = request.form.get("url", "").strip()
    plan = request.form.get("plan", "free").strip()

    max_pages = int(request.form.get("max_pages", CONFIG.DEFAULT_MAX_PAGES_FREE))
    max_depth = int(request.form.get("max_depth", CONFIG.DEFAULT_MAX_DEPTH))

    crawler = MiniCrawler(CONFIG.DEFAULT_USER_AGENT, CONFIG.REQUEST_TIMEOUT_S, CONFIG.MAX_FETCH_BYTES)
    crawl = crawler.crawl(url, max_pages=max_pages, max_depth=max_depth)

    checks_doc = load_json(CONFIG.CHECKS_PATH)
    scoring_model = load_json(CONFIG.SCORING_PATH)

    report_bits = run_checks(checks_doc, crawl, scoring_model, plan=plan)

    checks_schema_version = checks_doc.get("schema_version", "mvp-0.1")
    scoring_version = scoring_model.get("schema_version", "mvp-0.1")

    audit_json = make_audit_skeleton(
        target_url=url,
        normalized_url=getattr(crawl, "normalized_url", ""),
        host=getattr(crawl, "host", ""),
        schema_version=checks_schema_version,
        scoring_version=scoring_version,
        plan=plan,
        crawl_meta={
            "pages_requested": max_pages,
            "pages_crawled": len(getattr(crawl, "pages", {}) or {}),
            "crawl_depth": max_depth,
            "errors": getattr(crawl, "errors", []) or []
        }
    )

    audit_json["summary"]["overall_score"] = report_bits["summary"]["overall_score"]
    audit_json["summary"]["grade"] = report_bits["summary"]["grade"]
    audit_json["summary"]["caps_applied"] = report_bits["summary"].get("caps_applied", [])

    audit_json["category_scores"] = report_bits.get("category_scores", {})
    audit_json["checks"] = report_bits.get("checks", [])
    audit_json["recommendations"] = report_bits.get(
        "recommendations",
        {"critical": [], "important": [], "best_practice": []}
    )

    # сохраняем отчёт в БД и отдаём страницу по /report/<id>
    report_id = save_report(user_id, url, audit_json)
    return redirect(url_for("report_view", report_id=report_id))


@app.get("/report/<report_id>")
def report_view(report_id: str):
    user_id = get_or_create_user_id()
    audit_json = load_report(report_id, user_id)
    if not audit_json:
        abort(404)

    return render_template(
        "report.html",
        audit=audit_json,
        report_id=report_id,
        is_pro=is_user_pro(user_id),
        # pretty оставим для дебага (можно потом скрыть/удалить)
        pretty=json.dumps(audit_json, ensure_ascii=False, indent=2)
    )


@app.get("/api/me")
def api_me():
    user_id = get_or_create_user_id()
    return jsonify({"ok": True, "user_id": user_id, "is_pro": is_user_pro(user_id)})


@app.get("/api/report/<report_id>/pro/<check_id>")
def api_pro_fix(report_id: str, check_id: str):
    user_id = get_or_create_user_id()

    # report должен быть именно пользователя
    audit_json = load_report(report_id, user_id)
    if not audit_json:
        abort(404)

    if not is_user_pro(user_id):
        return jsonify({
            "ok": False,
            "reason": "PRO_REQUIRED",
            "message": "Upgrade to Pro to unlock step-by-step fix instructions."
        }), 402

    md = load_pro_fix_md(check_id)
    if not md:
        return jsonify({"ok": False, "reason": "NOT_FOUND"}), 404

    return jsonify({"ok": True, "check_id": check_id, "content_md": md})

@app.get("/pricing")
def pricing():
    return render_template("pricing.html")

# DEV ONLY: ручное включение Pro на 7 дней (чтобы тестить UI)
# УДАЛИМ/ЗАКРОЕМ когда подключишь Stripe
@app.get("/dev/toggle-pro")
def dev_toggle_pro_get():
    user_id = get_or_create_user_id()
    enable = request.args.get("enable", "1").strip() == "1"

    conn = get_db()
    try:
        if enable:
            pro_until = (datetime.utcnow() + timedelta(days=7)).replace(microsecond=0).isoformat() + "Z"
            conn.execute(
                "INSERT OR REPLACE INTO user_entitlements(user_id, is_pro, pro_until) VALUES (?,?,?)",
                (user_id, 1, pro_until)
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO user_entitlements(user_id, is_pro, pro_until) VALUES (?,?,?)",
                (user_id, 0, None)
            )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("index"))


@app.post("/dev/toggle-pro")
def dev_toggle_pro():
    user_id = get_or_create_user_id()
    enable = request.form.get("enable", "1").strip() == "1"

    conn = get_db()
    try:
        if enable:
            pro_until = (datetime.utcnow() + timedelta(days=7)).replace(microsecond=0).isoformat() + "Z"
            conn.execute(
                "INSERT OR REPLACE INTO user_entitlements(user_id, is_pro, pro_until) VALUES (?,?,?)",
                (user_id, 1, pro_until)
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO user_entitlements(user_id, is_pro, pro_until) VALUES (?,?,?)",
                (user_id, 0, None)
            )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("index"))

@app.get("/waitlist")
def waitlist():
    plan = request.args.get("plan", "pro")
    return render_template("waitlist.html", plan=plan)

@app.post("/waitlist")
def waitlist_post():
    email = (request.form.get("email") or "").strip().lower()
    plan = request.form.get("plan") or "pro"
    # TODO: сохранить email куда-то (sqlite table waitlist / или просто лог)
    return render_template("waitlist_done.html", email=email, plan=plan)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
