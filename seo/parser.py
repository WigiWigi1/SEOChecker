import json
from bs4 import BeautifulSoup

SOFT404_PHRASES = [
    "page not found", "not found", "404", "doesn't exist", "does not exist",
    "we can’t find", "we can't find", "error 404", "page was not found"
]

def parse_html(html: str):
    return BeautifulSoup(html or "", "lxml")

def extract_head_data(soup: BeautifulSoup) -> dict:
    head = soup.head
    title = (head.title.get_text(strip=True) if head and head.title else "") if soup else ""
    meta_desc = ""
    meta_robots = ""
    viewport = ""
    og = {}
    canonicals = []
    html_lang = ""
    jsonld_blocks = []

    if soup and soup.html and soup.html.has_attr("lang"):
        html_lang = (soup.html.get("lang") or "").strip()

    if head:
        for m in head.find_all("meta"):
            name = (m.get("name") or "").strip().lower()
            prop = (m.get("property") or "").strip().lower()
            content = (m.get("content") or "").strip()
            if name == "description":
                meta_desc = content
            elif name == "robots":
                meta_robots = content.lower()
            elif name == "viewport":
                viewport = content
            if prop.startswith("og:"):
                og[prop] = content

        for link in head.find_all("link"):
            rel = " ".join((link.get("rel") or [])).lower()
            href = (link.get("href") or "").strip()
            if "canonical" in rel and href:
                canonicals.append(href)

        for script in head.find_all("script"):
            t = (script.get("type") or "").strip().lower()
            if t == "application/ld+json":
                txt = (script.string or "").strip()
                if txt:
                    jsonld_blocks.append(txt)

    return {
        "title": title,
        "meta_description": meta_desc,
        "meta_robots": meta_robots,
        "viewport": viewport,
        "og": og,
        "canonicals": canonicals,
        "html_lang": html_lang,
        "jsonld_blocks": jsonld_blocks
    }

def extract_body_signals(soup: BeautifulSoup) -> dict:
    h1_count = 0
    internal_links = []
    all_links = []
    images = []
    text = ""

    if soup:
        h1_count = len(soup.find_all("h1"))
        for a in soup.find_all("a"):
            href = (a.get("href") or "").strip()
            if href:
                all_links.append(href)
        for img in soup.find_all("img"):
            images.append({
                "alt": (img.get("alt") or "").strip(),
                "src": (img.get("src") or "").strip()
            })
        text = soup.get_text(" ", strip=True)

    soft404 = any(p in (text or "").lower() for p in SOFT404_PHRASES)

    return {
        "h1_count": h1_count,
        "all_links": all_links,
        "images": images,
        "text": text,
        "soft404_signal": soft404
    }

def parse_jsonld_blocks(blocks: list[str]) -> dict:
    errors = 0
    for b in blocks or []:
        try:
            json.loads(b)
        except Exception:
            errors += 1
    return {"jsonld_count": len(blocks or []), "jsonld_parse_errors": errors}
