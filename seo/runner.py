from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# runner relies on crawler PageData/CrawlResult structures
# but we keep it duck-typed to avoid import cycles


# -------------------------
# Helpers: crawl pages access
# -------------------------

def _pages_list(crawl_result: Any) -> List[Any]:
    """
    Normalizes crawl_result.pages to a list[PageData].

    Supports:
      - CrawlResult.pages as dict[url] = PageData  (your current crawler.py)
      - CrawlResult.pages as list[PageData]
      - dict-like { "pages": ... }
    """
    pages = getattr(crawl_result, "pages", None)
    if pages is None and isinstance(crawl_result, dict):
        pages = crawl_result.get("pages")

    if pages is None:
        return []

    if isinstance(pages, dict):
        return list(pages.values())

    return list(pages)


def _crawl_pages_count(crawl_result: Any) -> int:
    return len(_pages_list(crawl_result))


def _get_normalized_url(crawl_result: Any) -> Optional[str]:
    v = getattr(crawl_result, "normalized_url", None)
    if v is None and isinstance(crawl_result, dict):
        v = crawl_result.get("normalized_url")
    return v


def _get_host(crawl_result: Any) -> Optional[str]:
    v = getattr(crawl_result, "host", None)
    if v is None and isinstance(crawl_result, dict):
        v = crawl_result.get("host")
    return v


def _pick_pages(scope: str, crawl_result: Any, limit: int = 10) -> List[Any]:
    pages = _pages_list(crawl_result)
    if not pages:
        return []

    if scope == "homepage":
        normalized = _get_normalized_url(crawl_result)
        if normalized:
            for p in pages:
                if getattr(p, "final_url", None) == normalized or getattr(p, "url", None) == normalized:
                    return [p]
        # fallback: first page
        return [pages[0]]

    if scope == "sample_set":
        return pages[:limit]

    if scope == "site":
        return pages

    # default fallback
    return pages[:limit]


# -------------------------
# Scoring primitives
# -------------------------

def _status_to_value(status: str, scoring_model: Dict[str, Any]) -> Optional[float]:
    rv = scoring_model["scoring"]["result_values"]
    # JSON null loads as None
    return rv.get(status, None)


def _severity_weight(sev: str, scoring_model: Dict[str, Any]) -> float:
    return float(scoring_model["scoring"]["severity_weights"].get(sev, 0.0))


def _category_weight(cat: str, scoring_model: Dict[str, Any]) -> float:
    return float(scoring_model["scoring"]["category_weights"].get(cat, 0.0))


def _grade_for(score: float, scoring_model: Dict[str, Any]) -> str:
    grading = scoring_model["scoring"]["grading"]
    # ensure sorted by min desc
    grading_sorted = sorted(grading, key=lambda x: x["min"], reverse=True)
    for g in grading_sorted:
        if score >= float(g["min"]):
            return str(g["grade"])
    return "D"


# -------------------------
# Method implementations
# -------------------------

def _method_http_status_200(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    sc = int(getattr(p, "status_code", 0) or 0)
    ok = 200 <= sc < 300
    return ("pass" if ok else "fail"), {"status_code": sc, "final_url": getattr(p, "final_url", None)}, {}


def _method_sample_status_200(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    if not pages:
        return "na", None, {"note": "No pages"}
    bad = []
    for p in pages:
        sc = int(getattr(p, "status_code", 0) or 0)
        if not (200 <= sc < 300):
            bad.append({"url": getattr(p, "final_url", getattr(p, "url", "")), "status_code": sc})
    if not bad:
        return "pass", {"checked": len(pages)}, {}
    ratio_ok = (len(pages) - len(bad)) / max(1, len(pages))
    if ratio_ok >= 0.9:
        return "partial", {"bad": bad, "checked": len(pages)}, {}
    return "fail", {"bad": bad, "checked": len(pages)}, {}


def _method_https_enabled(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    final_url = getattr(p, "final_url", getattr(p, "url", "")) or ""
    scheme = urlparse(final_url).scheme.lower()
    return ("pass" if scheme == "https" else "fail"), {"final_url": final_url, "scheme": scheme}, {}


def _method_noindex_absent(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    robots_meta = (head.get("meta_robots") or "").lower()
    xrobots = (getattr(p, "headers", {}) or {}).get("x-robots-tag", "")
    xrobots_l = (xrobots or "").lower()
    has_noindex = ("noindex" in robots_meta) or ("noindex" in xrobots_l)
    return ("fail" if has_noindex else "pass"), {"meta_robots": robots_meta, "x_robots_tag": xrobots}, {}


def _method_viewport_present(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    # best practice / mobile
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    vp = head.get("meta_viewport")
    return ("pass" if vp else "fail"), {"meta_viewport": vp}, {}


def _method_html_lang_present(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    lang = head.get("html_lang")
    return ("pass" if lang else "fail"), {"html_lang": lang}, {}


def _method_title_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    have = 0
    for p in pages:
        head = getattr(p, "head", {}) or {}
        title = (head.get("title") or "").strip()
        if title:
            have += 1
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    return "fail", {"ratio": ratio, "have": have, "total": len(pages)}, {}


def _method_meta_description_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    have = 0
    for p in pages:
        head = getattr(p, "head", {}) or {}
        desc = (head.get("meta_description") or "").strip()
        if desc:
            have += 1
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    return "fail", {"ratio": ratio, "have": have, "total": len(pages)}, {}


def _method_h1_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.9))
    have = 0
    for p in pages:
        body = getattr(p, "body", {}) or {}
        h1_count = int(body.get("h1_count") or 0)
        if h1_count >= 1:
            have += 1
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    if ratio >= 0.75:
        return "partial", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    return "fail", {"ratio": ratio, "have": have, "total": len(pages)}, {}


def _method_multiple_h1_warning(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    # best practice: warn only
    multi = []
    for p in pages:
        body = getattr(p, "body", {}) or {}
        h1_count = int(body.get("h1_count") or 0)
        if h1_count > 1:
            multi.append({"url": getattr(p, "final_url", getattr(p, "url", "")), "h1_count": h1_count})
    if not multi:
        return "pass", {"checked": len(pages)}, {}
    if len(multi) <= max(1, len(pages) // 5):
        return "partial", {"multi": multi, "checked": len(pages)}, {}
    return "fail", {"multi": multi, "checked": len(pages)}, {}


def _method_robots_exists(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    robots_txt = getattr(crawl_result, "robots_txt", None)
    robots_status = getattr(crawl_result, "robots_status", None)
    ok = bool(robots_txt) and (robots_status is None or (200 <= int(robots_status) < 300))
    return ("pass" if ok else "fail"), {"robots_status": robots_status, "has_robots": bool(robots_txt)}, {}


def _method_sitemap_exists(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    sitemap_xml = getattr(crawl_result, "sitemap_xml", None)
    sitemap_status = getattr(crawl_result, "sitemap_status", None)
    ok = bool(sitemap_xml) and (sitemap_status is None or (200 <= int(sitemap_status) < 300))
    return ("pass" if ok else "fail"), {"sitemap_status": sitemap_status, "has_sitemap": bool(sitemap_xml)}, {}


def _method_canonical_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    have = 0
    for p in pages:
        head = getattr(p, "head", {}) or {}
        canon = (head.get("canonical") or "").strip()
        if canon:
            have += 1
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "have": have, "total": len(pages)}, {}
    return "fail", {"ratio": ratio, "have": have, "total": len(pages)}, {}


def _method_og_tags_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    # info only
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    og = head.get("open_graph", {}) or {}
    has_any = any(bool(v) for v in og.values()) if isinstance(og, dict) else False
    return ("pass" if has_any else "fail"), {"open_graph": og}, {}


def _method_favicon_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    fav = head.get("favicon")
    return ("pass" if fav else "fail"), {"favicon": fav}, {}


# map method name -> function
_METHODS = {
    "http_status_200": lambda pages, params, crawl=None: _method_http_status_200(pages, params),
    "sample_http_status_200": lambda pages, params, crawl=None: _method_sample_status_200(pages, params),
    "https_enabled": lambda pages, params, crawl=None: _method_https_enabled(pages, params),
    "noindex_absent": lambda pages, params, crawl=None: _method_noindex_absent(pages, params),
    "viewport_present": lambda pages, params, crawl=None: _method_viewport_present(pages, params),
    "html_lang_present": lambda pages, params, crawl=None: _method_html_lang_present(pages, params),
    "title_present_ratio": lambda pages, params, crawl=None: _method_title_present_ratio(pages, params),
    "meta_description_present_ratio": lambda pages, params, crawl=None: _method_meta_description_present_ratio(pages, params),
    "h1_present_ratio": lambda pages, params, crawl=None: _method_h1_present_ratio(pages, params),
    "multiple_h1_warning": lambda pages, params, crawl=None: _method_multiple_h1_warning(pages, params),
    "robots_exists": lambda pages, params, crawl=None: _method_robots_exists(crawl, params),
    "sitemap_exists": lambda pages, params, crawl=None: _method_sitemap_exists(crawl, params),
    "canonical_present_ratio": lambda pages, params, crawl=None: _method_canonical_present_ratio(pages, params),
    "og_tags_info": lambda pages, params, crawl=None: _method_og_tags_info(pages, params),
    "favicon_info": lambda pages, params, crawl=None: _method_favicon_info(pages, params),
}


# -------------------------
# Execution wrapper
# -------------------------

def exec_method(method: str, applies_to: str, crawl_result: Any, params: Optional[Dict[str, Any]] = None) -> Tuple[str, Any, Dict[str, Any], List[str]]:
    params = params or {}
    pages = _pick_pages(applies_to, crawl_result)

    page_sample = []
    for p in pages[:5]:
        page_sample.append(getattr(p, "final_url", getattr(p, "url", "")))

    if not pages and applies_to in ("homepage", "sample_set", "site"):
        return "na", None, {"note": f"No pages available for this check (crawl returned {_crawl_pages_count(crawl_result)} pages)."}, page_sample

    fn = _METHODS.get(method)
    if not fn:
        return "na", None, {"note": f"Unknown/unsupported method: {method}"}, page_sample

    status, observed, extra = fn(pages, params, crawl=crawl_result)

    return status, observed, (extra or {}), page_sample


# -------------------------
# Main entry: run_checks
# -------------------------

def run_checks(checks_doc: Dict[str, Any], crawl_result: Any, scoring_model: Dict[str, Any], plan: str = "free") -> Dict[str, Any]:
    """
    Returns structure:
      {
        "checks": [ ... per check dict ... ],
        "category_scores": { ... },
        "summary": { "overall_score": ..., "grade": ..., "caps_applied": [...] },
        "recommendations": { "critical": [...], "important": [...], "best_practice": [...] }
      }

    This is designed to match the report shape you already print.
    """
    checks_list = checks_doc.get("checks", [])
    results: List[Dict[str, Any]] = []

    # Compute check results
    for c in checks_list:
        check_id = c.get("id")
        category = c.get("category")
        name = c.get("name", "")
        severity = c.get("severity", "info")
        is_best_practice = bool(c.get("is_best_practice", False))
        affects_indexing = bool(c.get("affects_indexing", False))
        applies_to = c.get("applies_to", ["sample_set"])
        if isinstance(applies_to, list):
            applies_to_str = ",".join(applies_to)
            applies_primary = applies_to[0] if applies_to else "sample_set"
        else:
            applies_to_str = str(applies_to)
            applies_primary = str(applies_to)

        method = c.get("method")
        params = c.get("params", {}) or {}

        status, observed, extra, page_sample = exec_method(method, applies_primary, crawl_result, params=params)

        score_value = _status_to_value(status, scoring_model)

        # explanation strings (simple, but consistent)
        if status == "na":
            details = extra.get("note", "Not applicable / not executed")
        else:
            details = ""

        results.append({
            "check_id": check_id,
            "category": category,
            "severity": severity,
            "is_best_practice": is_best_practice,
            "affects_indexing": affects_indexing,
            "status": status,
            "score_value": score_value,
            "applies_to": applies_to_str,
            "page_sample": page_sample,
            "observed": observed,
            "explanation": {
                "short": f"{name}: {'Not applicable / not executed' if status == 'na' else status}",
                "details": details
            },
            "fix_hint": {
                "priority": ("best_practice" if is_best_practice else ("important" if severity in ("critical", "high") else "normal")),
                "action": c.get("fix_action", ""),
                "who": c.get("fix_who", "developer"),
                "effort": c.get("fix_effort", "")
            }
        })

    # Category scoring
    category_scores: Dict[str, Optional[float]] = {}
    cats = scoring_model["scoring"]["category_weights"].keys()

    # for each category, compute weighted average based on severity weights
    for cat in cats:
        cat_checks = [r for r in results if r["category"] == cat and r["status"] != "na"]
        if not cat_checks:
            category_scores[cat] = None
            continue

        num = 0.0
        den = 0.0
        for r in cat_checks:
            sev_w = _severity_weight(r["severity"], scoring_model)
            val = r["score_value"]
            if val is None:
                continue
            num += float(val) * sev_w
            den += sev_w

        if den == 0:
            category_scores[cat] = None
        else:
            category_scores[cat] = round((num / den) * 100.0, 2)

    # Overall score: weighted sum of available categories
    overall_num = 0.0
    overall_den = 0.0
    for cat, score in category_scores.items():
        if score is None:
            continue
        w = _category_weight(cat, scoring_model)
        overall_num += float(score) * w
        overall_den += w

    overall_score = 0.0 if overall_den == 0 else (overall_num / overall_den)
    overall_score = float(round(overall_score, 2))

    # Caps (optional)
    caps_applied: List[Dict[str, Any]] = []
    caps = scoring_model["scoring"].get("caps", []) or []
    max_score = overall_score
    failed_ids = {r["check_id"] for r in results if r["status"] == "fail"}

    for cap in caps:
        trigger = cap.get("if_failed_any", []) or []
        if any(cid in failed_ids for cid in trigger):
            cap_max = float(cap.get("max_overall_score", 100))
            if max_score > cap_max:
                max_score = cap_max
                caps_applied.append({
                    "id": cap.get("id"),
                    "name": cap.get("name"),
                    "max_overall_score": cap_max
                })

    overall_score_capped = float(round(max_score, 2))
    grade = _grade_for(overall_score_capped, scoring_model)

    # Recommendations buckets
    rec_critical = []
    rec_important = []
    rec_best = []

    # Sort: severity desc, affects_indexing desc, category_weight desc
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    def sort_key(r: Dict[str, Any]) -> Tuple[int, int, float]:
        return (
            sev_rank.get(r["severity"], 0),
            1 if r.get("affects_indexing") else 0,
            _category_weight(r.get("category"), scoring_model)
        )

    failed = [r for r in results if r["status"] in ("fail", "partial")]
    failed_sorted = sorted(failed, key=sort_key, reverse=True)

    top_n = int(scoring_model["scoring"].get("recommendation_priority", {}).get("top_n", 10))
    for r in failed_sorted[:top_n]:
        item = {
            "check_id": r["check_id"],
            "category": r["category"],
            "severity": r["severity"],
            "status": r["status"],
            "short": r["explanation"]["short"],
            "details": r["explanation"].get("details", "")
        }
        if r["is_best_practice"]:
            rec_best.append(item)
        elif r["severity"] == "critical":
            rec_critical.append(item)
        else:
            rec_important.append(item)

    return {
        "summary": {
            "overall_score": overall_score_capped,
            "grade": grade,
            "caps_applied": caps_applied
        },
        "category_scores": category_scores,
        "checks": results,
        "recommendations": {
            "critical": rec_critical,
            "important": rec_important,
            "best_practice": rec_best
        }
    }
