"""Reader view: fetch an article page and boil it down to its title, pictures and text,
the way Safari's Reader or Flipboard's reader view does. Results are cached in the database."""
import html as htmlmod
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import lxml.html
import requests
from lxml.html.clean import Cleaner
from readability import Document

from fetcher import UA, TIMEOUT, domain_of, normalize_url

log = logging.getLogger("readable")

MAX_BYTES = 3_000_000        # a page bigger than this is cut off; the article is near the top anyway
MIN_WORDS = 60               # fewer than this and the phone shows the website instead
RETRY_FAILED_AFTER = 3600    # a page that failed is tried again after an hour
# story links that are wrappers around something else, not articles
NO_READER_HOSTS = {"news.google.com", "flipboard.com", "youtube.com", "youtu.be", "twitter.com", "x.com"}

LAZY_SRC = ("data-src", "data-lazy-src", "data-original", "data-url")
LAZY_SRCSET = ("data-srcset", "data-lazy-srcset")
TRACKING_IMG = re.compile(r"(pixel|tracking|/track/|beacon|1x1|spacer|blank\.gif)", re.I)

_cleaner = Cleaner(
    scripts=True, javascript=True, comments=True, style=True, inline_style=True, links=True, meta=True,
    page_structure=True, processing_instructions=True, embedded=True, frames=True, forms=True,
    annoying_tags=True, remove_unknown_tags=False,
    allow_tags={"p", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "figure", "figcaption", "img", "a",
                "strong", "em", "b", "i", "u", "s", "br", "hr", "pre", "code", "table", "thead", "tbody", "tr",
                "td", "th", "sup", "sub", "span", "div", "cite", "time", "mark", "small"},
    kill_tags={"script", "style", "noscript", "iframe", "svg", "form", "button", "input", "select", "textarea",
               "nav", "aside", "footer", "header", "video", "audio", "canvas", "object", "embed", "template"},
    safe_attrs_only=True,
    safe_attrs=frozenset(["src", "srcset", "sizes", "href", "alt", "width", "height", "colspan", "rowspan"]),
)


def _meta(page, *names):
    """First <meta> value among `names` (property= or name=), from the raw page."""
    for name in names:
        pat = re.escape(name)
        m = (re.search(rf"<meta[^>]+(?:property|name)=[\"']{pat}[\"'][^>]+content=[\"']([^\"']+)", page, re.I)
             or re.search(rf"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"']{pat}[\"']", page, re.I))
        if m:
            return htmlmod.unescape(m.group(1)).strip()
    return None


def fetch_page(url):
    """Download an article page (the first MAX_BYTES). Returns (final_url, html). Raises on trouble."""
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
               "Accept-Language": "en-US,en;q=0.8"}
    with requests.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            raise ValueError(f"not a web page ({ctype.split(';')[0] or 'unknown type'})")
        chunk = b""
        deadline = time.monotonic() + TIMEOUT  # a slow-dripping server must not hold a worker
        for part in r.iter_content(65536):
            chunk += part
            if len(chunk) >= MAX_BYTES or time.monotonic() > deadline:
                break
        enc = r.encoding
        if not enc or enc.lower() == "iso-8859-1":  # requests' default when the header says nothing useful
            m = re.search(rb"charset=[\"']?([\w-]+)", chunk[:5000], re.I)
            enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
        try:
            return r.url, chunk.decode(enc, errors="replace")
        except LookupError:
            return r.url, chunk.decode("utf-8", errors="replace")


def _clean_title(title):
    """'Big News | Site Name' -> 'Big News' (when what's left is still a real headline)."""
    t = htmlmod.unescape(title or "").strip()
    m = re.match(r"^(.*\S)\s+[|–—-]\s+[^|–—-]{2,40}$", t)
    if m and len(m.group(1).split()) >= 2:
        t = m.group(1)
    return t or None


MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
BYLINE_HINT = re.compile(rf"(\b{MONTHS}\s+\d{{1,2}},?\s+\d{{4}}|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d+\s+(minutes?|hours?|days?)\s+ago\b|^\s*by\s+[A-Z])", re.I)


def _looks_like_byline(text, author=None):
    """A short block holding a date, 'x hours ago', 'By Someone' or the author's name."""
    words = text.split()
    if not words or len(words) > 14:
        return False
    if author and author.lower() in text.lower():
        return True
    return bool(BYLINE_HINT.search(text))


def _pick_src(img):
    """Lazy-loading sites park the real picture address in a data- attribute."""
    src = (img.get("src") or "").strip()
    if not src or src.startswith("data:"):
        for a in LAZY_SRC:
            if img.get(a):
                src = img.get(a).strip()
                break
    if not img.get("srcset"):
        for a in LAZY_SRCSET:
            if img.get(a):
                img.set("srcset", img.get(a))
                break
    return src


def _abs_srcset(srcset, base):
    parts = []
    for cand in srcset.split(","):
        bits = cand.strip().split()
        if bits:
            bits[0] = urljoin(base, bits[0])
            parts.append(" ".join(bits))
    return ", ".join(parts)


def _same_picture(a, b):
    """The hero picture and the body's first picture are often the same file at two sizes."""
    try:
        pa, pb = urlparse(a), urlparse(b)
        return pa.netloc.lower().removeprefix("www.") == pb.netloc.lower().removeprefix("www.") and pa.path == pb.path
    except Exception:
        return False


def extract(page, url):
    """Pure: raw HTML + its address -> dict(ok, reason, title, author, hero, body, words)."""
    out = {"ok": False, "reason": None, "title": None, "author": None, "hero": None, "body": "", "words": 0}
    hero = _meta(page, "og:image", "twitter:image", "og:image:url")
    out["hero"] = urljoin(url, hero) if hero else None
    author = _meta(page, "author", "article:author", "parsely-author", "sailthru.author")
    if author and not re.match(r"^https?://", author, re.I):
        out["author"] = author[:80]
    try:
        doc = Document(page, url=url)
        title = _meta(page, "og:title") or doc.short_title() or doc.title()
        root = lxml.html.fromstring(doc.summary(html_partial=True))
    except Exception as ex:
        log.debug("readability failed for %s: %s", url, ex)
        out["reason"] = "unreadable"
        return out
    out["title"] = _clean_title(title)

    # the headline is shown separately; a copy at the top of the text reads as a stutter
    for h in list(root.iter("h1")):
        h.drop_tree()
    # promote lazy-loaded picture addresses before the cleaner strips data- attributes
    for img in root.iter("img"):
        src = _pick_src(img)
        if src:
            img.set("src", src)
    root = _cleaner.clean_html(root)

    # absolute addresses; no tracking pixels; no links to anything but web pages
    for img in list(root.iter("img")):
        src = (img.get("src") or "").strip()
        tiny = img.get("width") in ("0", "1") or img.get("height") in ("0", "1")
        if not src or src.startswith("data:") or tiny or TRACKING_IMG.search(src):
            img.drop_tree()
            continue
        img.set("src", urljoin(url, src))
        if img.get("srcset"):
            img.set("srcset", _abs_srcset(img.get("srcset"), url))
        for a in ("width", "height"):  # the phone sizes pictures with CSS
            img.attrib.pop(a, None)
    for a in list(root.iter("a")):
        href = urljoin(url, (a.get("href") or "").strip())
        if not re.match(r"^https?://", href, re.I):
            a.drop_tag()  # keep the words, lose the link
            continue
        a.set("href", href)
    imgs = list(root.iter("img"))
    if out["hero"] and imgs and _same_picture(imgs[0].get("src"), out["hero"]):
        imgs[0].drop_tree()  # the phone shows the hero up top; don't show it twice
    # a byline strip ("Jane Doe  Oct 23, 2026  Security") at the top: the phone shows its own
    for node in list(root)[:2]:
        if node.find(".//img") is None and _looks_like_byline(node.text_content(), out["author"]):
            node.drop_tree()
    # empty leftovers (a <p> that only held a script, a figure whose picture was a pixel)
    for node in list(root.iter("p", "div", "span", "figure", "li", "figcaption")):
        if node is root or node.getparent() is None:
            continue
        if not node.text_content().strip() and node.find(".//img") is None:
            node.drop_tree()

    out["words"] = len(root.text_content().split())
    out["body"] = (root.text or "") + "".join(lxml.html.tostring(c, encoding="unicode") for c in root)
    if out["words"] >= MIN_WORDS:
        out["ok"] = True
    else:
        out["reason"] = "short"
    return out


def _public(row):
    return {"ok": bool(row["ok"]), "reason": row["reason"], "url": row["url"], "title": row["title"],
            "author": row["author"], "hero": row["hero"], "body": row["body"] if row["ok"] else "",
            "words": row["words"]}


def get_article(url, db):
    """What the phone asks for: the reader version of a story, from the cache when we have it."""
    key = normalize_url(url)
    cached = db.get_readable(key)
    now = time.time()
    if cached and (cached["ok"] or now - cached["fetched"] < RETRY_FAILED_AFTER):
        return _public(cached)
    row = {"norm_url": key, "url": url, "ok": 0, "reason": None, "title": None, "author": None,
           "hero": None, "body": "", "words": 0, "fetched": now}
    host = domain_of(url)
    if host in NO_READER_HOSTS or any(host.endswith("." + h) for h in NO_READER_HOSTS):
        row["reason"] = "wrapper"
    else:
        try:
            final, page = fetch_page(url)
            row["url"] = final
            ext = extract(page, final)
            row.update(ok=int(ext["ok"]), reason=ext["reason"], title=ext["title"], author=ext["author"],
                       hero=ext["hero"], body=ext["body"], words=ext["words"])
        except Exception as ex:
            log.info("reader view failed for %s: %s", url, str(ex)[:160])
            row["reason"] = "fetch"
    db.put_readable(row)
    return _public(row)
