import json
from typing import Dict, List, Any

def load_scoring(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_scores(check_results: List[Dict[str, Any]], scoring_model: dict) -> dict:
    s = scoring_model["scoring"]
    cat_weights = s["category_weights"]
    sev_weights = s["severity_weights"]
    rv = s["result_values"]

    # group checks by category
    cats = {}
    for r in check_results:
        cat = r["category"]
        cats.setdefault(cat, []).append(r)

    # category score 0..100 or null
    category_scores: Dict[str, float | None] = {}
    category_score01: Dict[str, float | None] = {}

    for cat, items in cats.items():
        num = 0.0
        den = 0.0
        for it in items:
            status = it["status"]
            severity = it["severity"]
            val = rv.get(status)
            if val is None:  # NA
                continue
            w = float(sev_weights.get(severity, 0.0))
            num += float(val) * w
            den += w
        if den == 0:
            category_scores[cat] = None
            category_score01[cat] = None
        else:
            score01 = num / den
            category_score01[cat] = score01
            category_scores[cat] = round(score01 * 100)

    # overall raw
    overall_raw = 0.0
    for cat, w in cat_weights.items():
        sc01 = category_score01.get(cat)
        if sc01 is None:
            continue
        overall_raw += sc01 * float(w)
    overall_raw = round(overall_raw * 100)

    # caps
    caps_applied = []
    final_score = float(overall_raw)
    failed_ids = {r["check_id"] for r in check_results if r["status"] == "fail"}

    for cap in s.get("caps", []):
        trigger = cap.get("if_failed_any", [])
        if any(cid in failed_ids for cid in trigger):
            max_score = float(cap["max_overall_score"])
            if final_score > max_score:
                final_score = max_score
            caps_applied.append({
                "cap_id": cap.get("name", cap.get("id", "cap")),
                "max_score": int(max_score),
                "triggered_by": [cid for cid in trigger if cid in failed_ids]
            })

    final_score = int(round(final_score))

    # grade
    grade = "D"
    for band in s.get("grading", []):
        if final_score >= int(band["min"]):
            grade = band["grade"]
            break

    return {
        "overall_raw": int(overall_raw),
        "overall_score": final_score,
        "grade": grade,
        "caps_applied": caps_applied,
        "category_scores": category_scores
    }
