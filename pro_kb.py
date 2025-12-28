import os

PRO_KB_DIR = os.environ.get("PRO_KB_DIR", os.path.join(os.path.dirname(__file__), "private", "pro_kb"))

def load_pro_fix(check_id: str) -> str | None:
    # разрешаем только ID формата A-Z0-9_-
    safe = "".join([c for c in check_id if c.isalnum() or c in ("_", "-")])
    if safe != check_id or not check_id:
        return None

    # 1) md файл
    md_path = os.path.join(PRO_KB_DIR, f"{check_id}.md")
    if os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()

    # 2) можно расширить на JSON: {title, steps, examples, ...}
    return None
