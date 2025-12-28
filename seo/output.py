import uuid
from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def make_audit_skeleton(target_url: str, normalized_url: str, host: str, schema_version: str, scoring_version: str, plan: str, crawl_meta: dict):
    return {
        "audit_meta": {
            "audit_id": str(uuid.uuid4()),
            "schema_version": schema_version,
            "scoring_version": scoring_version,
            "target": {
                "url": target_url,
                "normalized_url": normalized_url,
                "host": host
            },
            "timestamp": now_iso(),
            "plan": plan,
            "crawl": crawl_meta
        },
        "summary": {
            "overall_score": None,
            "grade": None,
            "caps_applied": []
        },
        "category_scores": {},
        "checks": [],
        "recommendations": {
            "critical": [],
            "important": [],
            "best_practice": []
        }
    }

def make_check_result(check: dict, status: str, observed, extra: dict, crawl_result):
    # status must be pass|partial|fail|na
    severity = check.get("severity")
    is_best = bool(check.get("is_best_practice"))
    affects = bool(check.get("affects_indexing"))

    short = ""
    if status == "pass":
        short = "OK"
    elif status == "fail":
        short = "Failed"
    elif status == "partial":
        short = "Partially OK"
    else:
        short = "Not applicable / not executed"

    # priority bucket
    if status == "fail":
        if severity in ("critical",):
            pr = "critical"
        elif severity in ("high", "medium"):
            pr = "important"
        else:
            pr = "best_practice"
    else:
        pr = "best_practice" if is_best else "important"

    return {
        "check_id": check["id"],
        "category": check["category"],
        "severity": severity,
        "is_best_practice": is_best,
        "affects_indexing": affects,
        "status": status,
        "score_value": None,  # filled implicitly by scoring model; optional to store
        "applies_to": ",".join(check.get("applies_to", [])),
        "page_sample": list(crawl_result.pages.keys())[:1],
        "observed": observed,
        "explanation": {
            "short": f"{check.get('name')}: {short}",
            "details": extra.get("note") if extra and "note" in extra else ""
        },
        "fix_hint": {
            "priority": pr,
            "action": "",
            "who": "developer",
            "effort": ""
        }
    }

def build_recommendations(check_results: list, top_n: int = 10):
    critical, important, best = [], [], []
    for r in check_results:
        if r["status"] != "fail":
            continue
        sev = r["severity"]
        if sev == "critical":
            critical.append(r)
        elif sev in ("high", "medium"):
            important.append(r)
        else:
            best.append(r)

    def compact(r):
        return {
            "check_id": r["check_id"],
            "reason": r["explanation"]["short"],
            "suggested_fix": r["fix_hint"].get("action", "")
        }

    return {
        "critical": [compact(x) for x in critical[:top_n]],
        "important": [compact(x) for x in important[:top_n]],
        "best_practice": [compact(x) for x in best[:top_n]]
    }
