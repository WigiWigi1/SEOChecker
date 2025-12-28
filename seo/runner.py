from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import random
import string
import xml.etree.ElementTree as ET
import requests


# -------------------------
# Helpers: crawl pages access
# -------------------------

def _pages_list(crawl_result: Any) -> List[Any]:
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
        return [pages[0]]

    if scope == "sample_set":
        return pages[:limit]

    if scope == "site":
        return pages

    return pages[:limit]


# -------------------------
# Small URL helpers
# -------------------------

def _norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    # very lightweight normalization
    parsed = urlparse(u)
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    # keep query if exists for some checks
    q = ("?" + parsed.query) if parsed.query else ""
    return f"{scheme}://{netloc}{path}{q}" if scheme and netloc else u


def _preferred_parts(crawl_result: Any) -> Tuple[str, str]:
    normalized = _get_normalized_url(crawl_result) or ""
    p = urlparse(normalized)
    scheme = (p.scheme or "").lower()
    host = (p.netloc or "").lower()
    if not host:
        host = (_get_host(crawl_result) or "").lower()
    return scheme, host


def _url_host(u: str) -> str:
    return (urlparse(u).netloc or "").lower()


def _url_scheme(u: str) -> str:
    return (urlparse(u).scheme or "").lower()


def _url_path(u: str) -> str:
    return urlparse(u).path or "/"


# -------------------------
# Robots helpers (simple)
# -------------------------

def _parse_robots_for_star(robots_txt: str) -> Dict[str, List[str]]:
    # minimal parser: only User-agent: *
    disallow: List[str] = []
    allow: List[str] = []
    current_is_star = False

    for raw in (robots_txt or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            ua = line.split(":", 1)[1].strip()
            current_is_star = (ua == "*" or ua.lower() == "*")
            continue
        if not current_is_star:
            continue

        if low.startswith("disallow:"):
            path = line.split(":", 1)[1].strip() or ""
            disallow.append(path)
        elif low.startswith("allow:"):
            path = line.split(":", 1)[1].strip() or ""
            allow.append(path)

    # normalize: empty disallow means allow all, keep as ""
    return {"disallow": disallow, "allow": allow}


def _robots_allows_path(path: str, rules: Dict[str, List[str]]) -> bool:
    # longest-match wins, Allow overrides Disallow if same length
    path = path or "/"
    allow = rules.get("allow", []) or []
    disallow = rules.get("disallow", []) or []

    best_allow = ""
    best_disallow = ""

    for a in allow:
        if a == "":
            continue
        if path.startswith(a) and len(a) > len(best_allow):
            best_allow = a
    for d in disallow:
        if d == "":
            continue
        if path.startswith(d) and len(d) > len(best_disallow):
            best_disallow = d

    if best_allow and len(best_allow) >= len(best_disallow):
        return True
    if best_disallow:
        return False
    return True


# -------------------------
# XML sitemap helpers
# -------------------------

def _sitemap_extract_locs(xml_text: str, limit: int = 200) -> Tuple[List[str], bool, List[str]]:
    """
    Returns (locs, is_index, lastmod_list_sample)
    - supports urlset and sitemapindex
    """
    locs: List[str] = []
    lastmods: List[str] = []
    is_index = False
    if not xml_text:
        return locs, is_index, lastmods

    try:
        root = ET.fromstring(xml_text.strip())
    except Exception:
        return [], False, []

    tag = root.tag.lower()
    if "sitemapindex" in tag:
        is_index = True
        for sm in root.findall(".//{*}sitemap"):
            loc = sm.findtext("{*}loc") or ""
            lm = sm.findtext("{*}lastmod") or ""
            if loc:
                locs.append(loc.strip())
            if lm:
                lastmods.append(lm.strip())
            if len(locs) >= limit:
                break
        return locs, True, lastmods[:20]

    # urlset
    for u in root.findall(".//{*}url"):
        loc = u.findtext("{*}loc") or ""
        lm = u.findtext("{*}lastmod") or ""
        if loc:
            locs.append(loc.strip())
        if lm:
            lastmods.append(lm.strip())
        if len(locs) >= limit:
            break

    return locs, False, lastmods[:20]


# -------------------------
# Scoring primitives
# -------------------------

def _status_to_value(status: str, scoring_model: Dict[str, Any]) -> Optional[float]:
    rv = scoring_model["scoring"]["result_values"]
    return rv.get(status, None)


def _severity_weight(sev: str, scoring_model: Dict[str, Any]) -> float:
    return float(scoring_model["scoring"]["severity_weights"].get(sev, 0.0))


def _category_weight(cat: str, scoring_model: Dict[str, Any]) -> float:
    return float(scoring_model["scoring"]["category_weights"].get(cat, 0.0))


def _grade_for(score: float, scoring_model: Dict[str, Any]) -> str:
    grading = scoring_model["scoring"]["grading"]
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


def _method_sample_status_200_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    if not pages:
        return "na", None, {"note": "No pages"}

    bad = []
    for p in pages:
        sc = int(getattr(p, "status_code", 0) or 0)
        if not (200 <= sc < 300):
            bad.append({"url": getattr(p, "final_url", getattr(p, "url", "")), "status_code": sc})

    ratio_ok = (len(pages) - len(bad)) / max(1, len(pages))
    if not bad:
        return "pass", {"checked": len(pages), "ratio_ok": ratio_ok}, {}
    if ratio_ok >= min_ratio:
        return "partial", {"bad": bad, "checked": len(pages), "ratio_ok": ratio_ok}, {}
    return "fail", {"bad": bad, "checked": len(pages), "ratio_ok": ratio_ok}, {}


def _method_redirect_no_loop(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    chain = getattr(p, "redirect_chain", []) or []
    seen = set()
    loop = False
    for hop in chain:
        u = (hop.get("url") or "").strip()
        if not u:
            continue
        if u in seen:
            loop = True
            break
        seen.add(u)
    if loop:
        return "fail", {"redirect_chain": chain}, {}
    return "pass", {"redirect_chain_len": len(chain)}, {}


def _method_redirect_prefers_301(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    chain = getattr(p, "redirect_chain", []) or []
    if not chain:
        return "pass", {"note": "No redirects"}, {}
    bad = [h for h in chain if int(h.get("status") or 0) not in (301,)]
    if bad:
        return "partial", {"bad_hops": bad, "redirect_chain": chain}, {}
    return "pass", {"redirect_chain": chain}, {}


def _method_redirect_max_hops(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    max_hops = int(params.get("max_hops", 3))
    p = pages[0]
    chain = getattr(p, "redirect_chain", []) or []
    hops = len(chain)
    if hops <= max_hops:
        return "pass", {"hops": hops, "redirect_chain": chain}, {}
    if hops == max_hops + 1:
        return "partial", {"hops": hops, "redirect_chain": chain}, {}
    return "fail", {"hops": hops, "redirect_chain": chain}, {}


def _method_https_enabled(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    final_url = getattr(p, "final_url", getattr(p, "url", "")) or ""
    scheme = urlparse(final_url).scheme.lower()
    return ("pass" if scheme == "https" else "fail"), {"final_url": final_url, "scheme": scheme}, {}


def _method_noindex_absent(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    robots_meta = (head.get("meta_robots") or "").lower()
    headers = getattr(p, "headers", {}) or {}
    xrobots = headers.get("x-robots-tag", "")
    xrobots_l = (xrobots or "").lower()
    has_noindex = ("noindex" in robots_meta) or ("noindex" in xrobots_l)
    return ("fail" if has_noindex else "pass"), {"meta_robots": robots_meta, "x_robots_tag": xrobots}, {}


def _method_noindex_absent_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    ok = 0
    offenders = []
    for p in pages:
        st, obs, _ = _method_noindex_absent([p], {})
        if st == "pass":
            ok += 1
        else:
            offenders.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = ok / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "offenders": offenders[:5]}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "offenders": offenders[:5]}, {}
    return "fail", {"ratio": ratio, "offenders": offenders[:5]}, {}


def _method_no_auth_wall_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    ok = 0
    bad = []
    for p in pages:
        sc = int(getattr(p, "status_code", 0) or 0)
        if sc in (401, 403):
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
        else:
            ok += 1
    ratio = ok / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "bad": bad[:5]}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "bad": bad[:5]}, {}
    return "fail", {"ratio": ratio, "bad": bad[:5]}, {}


def _method_soft404_rate(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    max_ratio = float(params.get("max_ratio", 0.1))
    flagged = []
    for p in pages:
        body = getattr(p, "body", {}) or {}
        if bool(body.get("soft404_signal")):
            flagged.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = len(flagged) / max(1, len(pages))
    if ratio <= max_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio <= max_ratio * 2:
        return "partial", {"ratio": ratio, "flagged": flagged[:5]}, {}
    return "fail", {"ratio": ratio, "flagged": flagged[:5]}, {}


def _method_random_404_is_404(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    base = _get_normalized_url(crawl_result) or ""
    if not base:
        return "na", None, {"note": "No normalized_url"}

    rnd = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(12))
    test_url = base.rstrip("/") + f"/__seochecker_{rnd}__"

    try:
        r = requests.get(test_url, timeout=5, allow_redirects=True, headers={"User-Agent": "SEOCheckerBot/0.1"})
        sc = int(r.status_code or 0)
        # treat 404 as pass; 200 likely soft 404 page
        if sc == 404:
            return "pass", {"test_url": test_url, "status_code": sc}, {}
        if 200 <= sc < 300:
            return "fail", {"test_url": test_url, "status_code": sc}, {}
        return "partial", {"test_url": test_url, "status_code": sc}, {}
    except Exception as e:
        return "na", None, {"note": f"Request failed: {type(e).__name__}: {e}"}


def _method_viewport_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.9))
    have = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        vp = head.get("viewport") or head.get("meta_viewport")
        if vp:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.75:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_html_lang_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.9))
    have = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        lang = head.get("html_lang")
        if lang:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.75:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_title_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    have = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        title = (head.get("title") or "").strip()
        if title:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_title_length_warning(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    # warning only
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        t = (head.get("title") or "").strip()
        if not t:
            continue
        if len(t) < 30 or len(t) > 65:
            bad.append({"url": getattr(p, "final_url", getattr(p, "url", "")), "len": len(t)})
    if not bad:
        return "pass", {"checked": len(pages)}, {}
    if len(bad) <= max(1, len(pages) // 5):
        return "partial", {"bad": bad[:5]}, {}
    return "fail", {"bad": bad[:5]}, {}


def _method_meta_description_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    have = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        desc = (head.get("meta_description") or "").strip()
        if desc:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_h1_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.9))
    have = 0
    bad = []
    for p in pages:
        body = getattr(p, "body", {}) or {}
        h1_count = int(body.get("h1_count") or 0)
        if h1_count >= 1:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.75:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_multiple_h1_warning(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    multi = []
    for p in pages:
        body = getattr(p, "body", {}) or {}
        h1_count = int(body.get("h1_count") or 0)
        if h1_count > 1:
            multi.append({"url": getattr(p, "final_url", getattr(p, "url", "")), "h1_count": h1_count})
    if not multi:
        return "pass", {"checked": len(pages)}, {}
    if len(multi) <= max(1, len(pages) // 5):
        return "partial", {"multi": multi[:5], "checked": len(pages)}, {}
    return "fail", {"multi": multi[:5], "checked": len(pages)}, {}


def _method_robots_exists(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    robots_txt = getattr(crawl_result, "robots_txt", None)
    robots_status = getattr(crawl_result, "robots_status", None)
    ok = bool(robots_txt) and (robots_status is None or (200 <= int(robots_status) < 300))
    return ("pass" if ok else "fail"), {"robots_status": robots_status, "has_robots": bool(robots_txt)}, {}


def _method_robots_mentions_sitemap(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    robots_txt = (getattr(crawl_result, "robots_txt", None) or "")
    has = any(line.strip().lower().startswith("sitemap:") for line in robots_txt.splitlines())
    return ("pass" if has else "fail"), {"mentions_sitemap": has}, {}


def _method_robots_allows_pages_ratio(pages: List[Any], crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    robots_txt = getattr(crawl_result, "robots_txt", None)
    if not robots_txt:
        return "na", None, {"note": "robots.txt not available"}

    rules = _parse_robots_for_star(robots_txt)
    allowed = 0
    blocked = []
    for p in pages:
        u = getattr(p, "final_url", getattr(p, "url", "")) or ""
        path = _url_path(u)
        if _robots_allows_path(path, rules):
            allowed += 1
        else:
            blocked.append(u)

    ratio = allowed / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "blocked": blocked[:5]}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "blocked": blocked[:5]}, {}
    return "fail", {"ratio": ratio, "blocked": blocked[:5]}, {}


def _method_sitemap_exists(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    sitemap_xml = getattr(crawl_result, "sitemap_xml", None)
    sitemap_status = getattr(crawl_result, "sitemap_status", None)
    ok = bool(sitemap_xml) and (sitemap_status is None or (200 <= int(sitemap_status) < 300))
    return ("pass" if ok else "fail"), {"sitemap_status": sitemap_status, "has_sitemap": bool(sitemap_xml)}, {}


def _method_sitemap_parses(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    xml_text = getattr(crawl_result, "sitemap_xml", None) or ""
    if not xml_text:
        return "fail", {"note": "No sitemap XML"}, {}
    locs, is_index, lastmods = _sitemap_extract_locs(xml_text, limit=50)
    if not locs:
        return "fail", {"note": "Sitemap XML found but no <loc> entries parsed"}, {}
    return "pass", {"parsed_locs": len(locs), "is_index": is_index, "lastmod_sample": lastmods[:5]}, {}


def _method_sitemap_host_protocol_ratio(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    xml_text = getattr(crawl_result, "sitemap_xml", None) or ""
    if not xml_text:
        return "na", None, {"note": "No sitemap XML"}

    locs, _is_index, _ = _sitemap_extract_locs(xml_text, limit=200)
    if not locs:
        return "na", None, {"note": "No locs parsed"}

    pref_scheme, pref_host = _preferred_parts(crawl_result)
    ok = 0
    bad = []
    for u in locs:
        if _url_scheme(u) == pref_scheme and _url_host(u) == pref_host:
            ok += 1
        else:
            bad.append(u)
    ratio = ok / max(1, len(locs))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "sample_bad": bad[:5]}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "sample_bad": bad[:5]}, {}
    return "fail", {"ratio": ratio, "sample_bad": bad[:5]}, {}


def _method_sitemap_lastmod_info(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    xml_text = getattr(crawl_result, "sitemap_xml", None) or ""
    if not xml_text:
        return "na", None, {"note": "No sitemap XML"}
    _locs, _is_index, lastmods = _sitemap_extract_locs(xml_text, limit=200)
    have = len([x for x in lastmods if x])
    return "pass", {"lastmod_sample": lastmods[:10], "sample_count_with_lastmod": have}, {}


def _method_canonical_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    have = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        canon = (head.get("canonical") or "").strip()
        if canon:
            have += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = have / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_canonical_single_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.98))
    ok = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        canonicals = head.get("canonicals") or []
        if isinstance(canonicals, list) and len(canonicals) <= 1:
            ok += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = ok / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio, "bad": bad[:5]}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "bad": bad[:5]}, {}
    return "fail", {"ratio": ratio, "bad": bad[:5]}, {}


def _method_canonical_preferred_ratio(pages: List[Any], crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.95))
    pref_scheme, pref_host = _preferred_parts(crawl_result)

    ok = 0
    bad = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        canon = (head.get("canonical") or "").strip()
        if not canon:
            continue
        # if canonical is relative, treat as same host
        if canon.startswith("/"):
            ok += 1
            continue
        if _url_scheme(canon) == pref_scheme and _url_host(canon) == pref_host:
            ok += 1
        else:
            bad.append({"page": getattr(p, "final_url", getattr(p, "url", "")), "canonical": canon})

    ratio = ok / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.8:
        return "partial", {"ratio": ratio, "bad": bad[:5]}, {}
    return "fail", {"ratio": ratio, "bad": bad[:5]}, {}


def _method_trailing_slash_consistency(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    pages = _pages_list(crawl_result)
    if not pages:
        return "na", None, {"note": "No pages"}

    slash = 0
    noslash = 0
    for p in pages:
        u = getattr(p, "final_url", getattr(p, "url", "")) or ""
        path = _url_path(u)
        if path != "/" and path.endswith("/"):
            slash += 1
        elif path != "/":
            noslash += 1
    total = slash + noslash
    if total == 0:
        return "pass", {"note": "No non-root paths"}, {}
    # if both exist -> warning
    if slash > 0 and noslash > 0:
        return "partial", {"slash": slash, "noslash": noslash}, {}
    return "pass", {"slash": slash, "noslash": noslash}, {}


def _method_www_consistency(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    pages = _pages_list(crawl_result)
    if not pages:
        return "na", None, {"note": "No pages"}

    www = 0
    non = 0
    for p in pages:
        u = getattr(p, "final_url", getattr(p, "url", "")) or ""
        h = _url_host(u)
        if h.startswith("www."):
            www += 1
        else:
            non += 1
    if www > 0 and non > 0:
        return "fail", {"www": www, "non_www": non}, {}
    return "pass", {"www": www, "non_www": non}, {}


def _method_param_url_bloat_warning(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    pages = _pages_list(crawl_result)
    if not pages:
        return "na", None, {"note": "No pages"}

    with_q = 0
    for p in pages:
        u = getattr(p, "final_url", getattr(p, "url", "")) or ""
        if "?" in u:
            with_q += 1
    ratio = with_q / max(1, len(pages))
    if ratio <= 0.1:
        return "pass", {"ratio": ratio}, {}
    if ratio <= 0.3:
        return "partial", {"ratio": ratio}, {}
    return "fail", {"ratio": ratio}, {}


def _method_og_tags_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    og = head.get("og") or head.get("open_graph") or {}
    has_any = any(bool(v) for v in og.values()) if isinstance(og, dict) else False
    return ("pass" if has_any else "fail"), {"og": og}, {}


def _method_favicon_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    p = pages[0]
    head = getattr(p, "head", {}) or {}
    fav = head.get("favicon")
    return ("pass" if fav else "fail"), {"favicon": fav}, {}


def _method_noop_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    return "pass", {"note": "Info-only check"}, {}


def _method_duplicate_titles_ratio(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    max_ratio = float(params.get("max_ratio", 0.05))
    pages = _pages_list(crawl_result)
    titles = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        t = (head.get("title") or "").strip()
        if t:
            titles.append(t)
    if not titles:
        return "na", None, {"note": "No titles found"}
    dup = len(titles) - len(set(titles))
    ratio = dup / max(1, len(titles))
    if ratio <= max_ratio:
        return "pass", {"ratio": ratio, "duplicates": dup}, {}
    if ratio <= max_ratio * 2:
        return "partial", {"ratio": ratio, "duplicates": dup}, {}
    return "fail", {"ratio": ratio, "duplicates": dup}, {}


def _method_duplicate_descriptions_ratio(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    max_ratio = float(params.get("max_ratio", 0.1))
    pages = _pages_list(crawl_result)
    descs = []
    for p in pages:
        head = getattr(p, "head", {}) or {}
        d = (head.get("meta_description") or "").strip()
        if d:
            descs.append(d)
    if not descs:
        return "na", None, {"note": "No meta descriptions found"}
    dup = len(descs) - len(set(descs))
    ratio = dup / max(1, len(descs))
    if ratio <= max_ratio:
        return "pass", {"ratio": ratio, "duplicates": dup}, {}
    if ratio <= max_ratio * 2:
        return "partial", {"ratio": ratio, "duplicates": dup}, {}
    return "fail", {"ratio": ratio, "duplicates": dup}, {}


def _method_internal_links_present_ratio(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    min_ratio = float(params.get("min_ratio", 0.9))
    ok = 0
    bad = []
    for p in pages:
        body = getattr(p, "body", {}) or {}
        n = int(body.get("internal_links_count") or 0)
        if n > 0:
            ok += 1
        else:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    ratio = ok / max(1, len(pages))
    if ratio >= min_ratio:
        return "pass", {"ratio": ratio}, {}
    if ratio >= 0.75:
        return "partial", {"ratio": ratio, "missing": bad[:5]}, {}
    return "fail", {"ratio": ratio, "missing": bad[:5]}, {}


def _method_broken_internal_links_ratio(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    """
    Lightweight broken link check:
    - take up to 25 internal links from first pages
    - HEAD/GET them with short timeout
    """
    max_ratio = float(params.get("max_ratio", 0.01))
    pages = _pages_list(crawl_result)
    links = []
    for p in pages[:10]:
        body = getattr(p, "body", {}) or {}
        for u in (body.get("internal_links") or [])[:10]:
            links.append(u)
            if len(links) >= 25:
                break
        if len(links) >= 25:
            break

    if not links:
        return "na", None, {"note": "No internal links collected"}

    bad = []
    for u in links:
        try:
            r = requests.get(u, timeout=5, allow_redirects=True, headers={"User-Agent": "SEOCheckerBot/0.1"})
            sc = int(r.status_code or 0)
            if sc >= 400:
                bad.append({"url": u, "status": sc})
        except Exception:
            bad.append({"url": u, "status": "error"})

    ratio = len(bad) / max(1, len(links))
    if ratio <= max_ratio:
        return "pass", {"checked": len(links), "bad": bad[:5], "ratio": ratio}, {}
    if ratio <= max_ratio * 3:
        return "partial", {"checked": len(links), "bad": bad[:5], "ratio": ratio}, {}
    return "fail", {"checked": len(links), "bad": bad[:5], "ratio": ratio}, {}


def _method_click_depth_info(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    pages = _pages_list(crawl_result)
    if not pages:
        return "na", None, {"note": "No pages"}
    dist: Dict[int, int] = {}
    for p in pages:
        d = int(getattr(p, "depth", 0) or 0)
        dist[d] = dist.get(d, 0) + 1
    return "pass", {"depth_distribution": dist}, {}


def _method_jsonld_present_info(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    total = 0
    with_jsonld = 0
    for p in pages:
        total += 1
        j = getattr(p, "jsonld", {}) or {}
        if int(j.get("jsonld_count") or 0) > 0:
            with_jsonld += 1
    ratio = with_jsonld / max(1, total)
    return "pass", {"ratio_pages_with_jsonld": ratio, "with_jsonld": with_jsonld, "total": total}, {}


def _method_jsonld_parse_warning(pages: List[Any], params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    bad = []
    for p in pages:
        j = getattr(p, "jsonld", {}) or {}
        if int(j.get("jsonld_parse_errors") or 0) > 0:
            bad.append(getattr(p, "final_url", getattr(p, "url", "")))
    if not bad:
        return "pass", {"checked": len(pages)}, {}
    if len(bad) <= max(1, len(pages) // 5):
        return "partial", {"bad": bad[:5]}, {}
    return "fail", {"bad": bad[:5]}, {}


def _method_trust_page_exists(crawl_result: Any, params: Dict[str, Any]) -> Tuple[str, Any, Dict[str, Any]]:
    kind = (params.get("kind") or "").lower()
    pages = _pages_list(crawl_result)
    urls = [getattr(p, "final_url", getattr(p, "url", "")) or "" for p in pages]

    patterns = {
        "contact": ["contact", "kontakt"],
        "about": ["about", "o-nas", "about-us"],
        "privacy": ["privacy", "gdpr", "privacy-policy", "ochrana-osobnich-udaju"],
        "terms": ["terms", "tos", "terms-and-conditions", "obchodni-podminky"]
    }
    pats = patterns.get(kind, [kind]) if kind else [kind]
    found = []
    for u in urls:
        lu = u.lower()
        if any(p in lu for p in pats if p):
            found.append(u)
    if found:
        return "pass", {"found": found[:5]}, {}
    return "fail", {"note": f"No obvious {kind} page found in crawled URLs."}, {}


# map method name -> function
_METHODS = {
    # status/redirects
    "http_status_200": lambda pages, params, crawl=None: _method_http_status_200(pages, params),
    "sample_status_200_ratio": lambda pages, params, crawl=None: _method_sample_status_200_ratio(pages, params),
    "sample_http_status_200": lambda pages, params, crawl=None: _method_sample_status_200_ratio(pages, params),  # alias
    "redirect_no_loop": lambda pages, params, crawl=None: _method_redirect_no_loop(pages, params),
    "redirect_prefers_301": lambda pages, params, crawl=None: _method_redirect_prefers_301(pages, params),
    "redirect_max_hops": lambda pages, params, crawl=None: _method_redirect_max_hops(pages, params),

    # indexing/crawlability
    "https_enabled": lambda pages, params, crawl=None: _method_https_enabled(pages, params),
    "noindex_absent": lambda pages, params, crawl=None: _method_noindex_absent(pages, params),
    "noindex_absent_ratio": lambda pages, params, crawl=None: _method_noindex_absent_ratio(pages, params),
    "no_auth_wall_ratio": lambda pages, params, crawl=None: _method_no_auth_wall_ratio(pages, params),
    "soft404_rate": lambda pages, params, crawl=None: _method_soft404_rate(pages, params),
    "random_404_is_404": lambda pages, params, crawl=None: _method_random_404_is_404(crawl, params),

    # robots/sitemaps
    "robots_exists": lambda pages, params, crawl=None: _method_robots_exists(crawl, params),
    "robots_mentions_sitemap": lambda pages, params, crawl=None: _method_robots_mentions_sitemap(crawl, params),
    "robots_allows_pages_ratio": lambda pages, params, crawl=None: _method_robots_allows_pages_ratio(pages, crawl, params),

    "sitemap_exists": lambda pages, params, crawl=None: _method_sitemap_exists(crawl, params),
    "sitemap_parses": lambda pages, params, crawl=None: _method_sitemap_parses(crawl, params),
    "sitemap_host_protocol_ratio": lambda pages, params, crawl=None: _method_sitemap_host_protocol_ratio(crawl, params),
    "sitemap_lastmod_info": lambda pages, params, crawl=None: _method_sitemap_lastmod_info(crawl, params),

    # canonical/duplicates
    "canonical_present_ratio": lambda pages, params, crawl=None: _method_canonical_present_ratio(pages, params),
    "canonical_single_ratio": lambda pages, params, crawl=None: _method_canonical_single_ratio(pages, params),
    "canonical_preferred_ratio": lambda pages, params, crawl=None: _method_canonical_preferred_ratio(pages, crawl, params),
    "trailing_slash_consistency": lambda pages, params, crawl=None: _method_trailing_slash_consistency(crawl, params),
    "www_consistency": lambda pages, params, crawl=None: _method_www_consistency(crawl, params),
    "param_url_bloat_warning": lambda pages, params, crawl=None: _method_param_url_bloat_warning(crawl, params),

    # mobile
    "viewport_present_ratio": lambda pages, params, crawl=None: _method_viewport_present_ratio(pages, params),
    "html_lang_present_ratio": lambda pages, params, crawl=None: _method_html_lang_present_ratio(pages, params),
    # simple placeholder
    "robots_blocks_assets_warning": lambda pages, params, crawl=None: ("na", None, {"note": "Not implemented yet"}, {}),

    # onpage basics
    "title_present_ratio": lambda pages, params, crawl=None: _method_title_present_ratio(pages, params),
    "title_length_warning": lambda pages, params, crawl=None: _method_title_length_warning(pages, params),
    "meta_description_present_ratio": lambda pages, params, crawl=None: _method_meta_description_present_ratio(pages, params),
    "h1_present_ratio": lambda pages, params, crawl=None: _method_h1_present_ratio(pages, params),
    "multiple_h1_warning": lambda pages, params, crawl=None: _method_multiple_h1_warning(pages, params),
    "noop_info": lambda pages, params, crawl=None: _method_noop_info(pages, params),
    "duplicate_titles_ratio": lambda pages, params, crawl=None: _method_duplicate_titles_ratio(crawl, params),
    "duplicate_descriptions_ratio": lambda pages, params, crawl=None: _method_duplicate_descriptions_ratio(crawl, params),

    # architecture
    "internal_links_present_ratio": lambda pages, params, crawl=None: _method_internal_links_present_ratio(pages, params),
    "broken_internal_links_ratio": lambda pages, params, crawl=None: _method_broken_internal_links_ratio(crawl, params),
    "click_depth_info": lambda pages, params, crawl=None: _method_click_depth_info(crawl, params),

    # structured data
    "jsonld_present_info": lambda pages, params, crawl=None: _method_jsonld_present_info(pages, params),
    "jsonld_parse_warning": lambda pages, params, crawl=None: _method_jsonld_parse_warning(pages, params),

    # trust
    "trust_page_exists": lambda pages, params, crawl=None: _method_trust_page_exists(crawl, params),

    # brand info
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

    ret = fn(pages, params, crawl=crawl_result)

    # поддержим оба формата: (status, observed, extra) и (status, observed, extra, ...)
    if isinstance(ret, tuple) and len(ret) >= 3:
        status, observed, extra = ret[0], ret[1], ret[2]
    else:
        # на всякий случай — чтобы не падало
        status, observed, extra = "na", None, {"note": "Bad method return format"}

    return status, observed, (extra or {}), page_sample


# -------------------------
# Main entry: run_checks
# -------------------------

def run_checks(checks_doc: Dict[str, Any], crawl_result: Any, scoring_model: Dict[str, Any], plan: str = "free") -> Dict[str, Any]:
    checks_list = checks_doc.get("checks", [])
    results: List[Dict[str, Any]] = []

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

        details = extra.get("note", "Not applicable / not executed") if status == "na" else ""

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

    category_scores: Dict[str, Optional[float]] = {}
    cats = scoring_model["scoring"]["category_weights"].keys()

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

        category_scores[cat] = None if den == 0 else round((num / den) * 100.0, 2)

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

    rec_critical = []
    rec_important = []
    rec_best = []

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
