"""Web access behind the webfetch and websearch tools - free by default, keys only buy quota.

    fetch(url, fmt, fallback="firecrawl") -> {"status", "url", "title", "source", "kind", "text"}
    search(query, n, providers=("firecrawl", "exa")) -> {"provider", "keyed", "results" | "text"}
    html_to_markdown(html, base_url)      html_to_text(html)

fetch follows opencode's webfetch: a plain GET with a browser User-Agent and an Accept header that
prefers markdown, then HTML converted to markdown so headings and links survive for the agent to
follow. Ours adds two things: every redirect hop is re-checked against the non-public-address
guard (opencode leaves that to the permission prompt), and when a page has a <main> or <article>
only that part is converted, which drops navigation and footers. When a direct fetch is blocked
(403 / 429 / 503, a Cloudflare challenge), the page is a PDF, or the HTML only renders with
JavaScript, it falls back to Firecrawl's scrape.

search tries providers in order: Firecrawl /v2/search, then Exa's hosted MCP endpoint (the one
opencode's websearch calls). Both answer without an API key, rate-limited per IP; FIRECRAWL_API_KEY
or EXA_API_KEY in .env only raise the limits. Keys are sent as headers and scrubbed from any error.
"""
import ipaddress
import json
import os
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()  # keys may be optional, but when .env has one it must be used even if llm_handler is not loaded

FIRECRAWL = "https://api.firecrawl.dev/v2"
EXA_MCP = "https://mcp.exa.ai/mcp"
KEY_ENV = {"firecrawl": "FIRECRAWL_API_KEY", "exa": "EXA_API_KEY"}
FALLBACKS = ("", "firecrawl")                # webfetch's fallback: none, or Firecrawl scrape
FORMATS = ("markdown", "text", "html")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36")   # opencode's
ACCEPT = {   # opencode's: ask for markdown first - some sites serve it to agents directly
    "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
    "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
    "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1",
}
MAX_BYTES = 2 * 1024 * 1024
MAX_HOPS = 5
BLOCKED = (401, 403, 429, 503)


# --------------------------------------------------------------------------- keys and guards

def key(provider: str) -> str | None:
    return os.getenv(KEY_ENV[provider]) or None


def scrub(text: str) -> str:
    """No provider key ever comes back in an observation, a log line or the trace."""
    for name in KEY_ENV.values():
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "***")
    return text


def guard(url: str) -> None:
    """http(s) only, and only to public addresses: no localhost, LAN or cloud metadata."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"only http(s) URLs are allowed, got '{url}'")
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"'{u.hostname}' resolves to a non-public address ({ip}); refused")


def check_config(web_cfg: dict) -> None:
    unknown = [p for p in web_cfg.get("search", []) if p not in SEARCH]
    if unknown:
        raise ValueError(f"web.search: unknown provider(s) {unknown}; have: {', '.join(SEARCH)}")
    if web_cfg.get("fetch_fallback", "firecrawl") not in FALLBACKS:
        raise ValueError(f"web.fetch_fallback must be one of {FALLBACKS}")


# --------------------------------------------------------------------------- HTML conversion

def charset(content_type: str) -> str:
    """The charset the server names, else UTF-8. requests falls back to ISO-8859-1 for text/* without
    one (the old HTTP default), which turns UTF-8 pages - python.org, any Thai site - into mojibake."""
    m = re.search(r"charset=[\"']?([\w.:-]+)", content_type or "", re.I)
    return m.group(1) if m else "utf-8"


SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "button", "select", "form"}
BOILERPLATE = {"nav", "footer", "aside"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK = {"p", "div", "section", "article", "main", "header", "br", "hr", "table", "ul", "ol",
         "blockquote", "figure", "dl", "dt", "dd"}


class _Region:
    """One element being tracked by its own tag - kept (main content) or skipped (script, hidden) -
    so nested elements of the same tag are counted and the region ends at its real end tag.
    Void tags (<br>, <input hidden>) never open a region: they have no end tag to close it."""

    def __init__(self):
        self.tag, self.depth = None, 0

    def enter(self, tag):
        if self.depth:
            self.depth += tag == self.tag
        elif tag not in VOID:
            self.tag, self.depth = tag, 1

    def nested(self, tag):
        if self.depth and tag == self.tag:
            self.depth += 1

    def leave(self, tag):
        if self.depth and tag == self.tag:
            self.depth -= 1


class _Markdown(HTMLParser):
    """Headings, links, lists, code and tables - what an agent needs to read a page and follow it.
    `keep` limits output to the main content (<main>, an element with role="main", or <article>);
    without it, obvious boilerplate (nav, footer, aside) is dropped instead."""

    def __init__(self, base_url: str, keep: str | None):
        super().__init__(convert_charrefs=True)
        self.base, self.keep = base_url, keep
        self.out, self.title = [], ""
        self.kept, self.skipped = _Region(), _Region()
        self.pre, self.in_title = 0, False
        self.lists, self.links = [], []

    def _is_main(self, tag, a) -> bool:
        return tag == self.keep or (self.keep == "role=main" and a.get("role") == "main")

    def _on(self) -> bool:
        return not self.skipped.depth and (self.keep is None or self.kept.depth > 0)

    def _emit(self, s: str):
        if self._on():
            self.out.append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if self.kept.depth:
            self.kept.nested(tag)
        elif self.keep and self._is_main(tag, a):
            self.kept.enter(tag)
        if self.skipped.depth:
            self.skipped.nested(tag)
            return
        if tag in SKIP or (self.keep is None and tag in BOILERPLATE) \
                or "hidden" in a or a.get("aria-hidden") == "true":
            self.skipped.enter(tag)
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self._emit("\n" + "  " * depth + (f"{kind[1]}. " if kind[0] == "ol" else "- "))
        elif tag == "pre":
            self.pre += 1
            self._emit("\n\n```\n")
        elif tag == "code" and not self.pre:
            self._emit("`")
        elif tag == "a":
            self.links.append((a.get("href") or "", len(self.out)))
        elif tag == "tr":
            self._emit("\n|")
        elif tag in ("td", "th"):
            self._emit(" ")
        elif tag in BLOCK:
            self._emit("\n\n" if tag in ("p", "blockquote", "table") else "\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.skipped.depth:
            self.skipped.leave(tag)
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            if self._on() and start <= len(self.out):
                text = " ".join("".join(self.out[start:]).split())
                if text and href and not href.startswith(("#", "javascript:", "mailto:")):
                    self.out[start:] = [f"[{text}]({urljoin(self.base, href)})"]
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self._emit("\n")
        elif tag == "pre" and self.pre:
            self.pre -= 1
            self._emit("\n```\n\n")
        elif tag == "code" and not self.pre:
            self._emit("`")
        elif tag in ("td", "th"):
            self._emit(" |")
        elif re.fullmatch(r"h[1-6]", tag) or tag in BLOCK:
            self._emit("\n")
        self.kept.leave(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        self._emit(data if self.pre else re.sub(r"\s+", " ", data))


def _tidy(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_markdown(html: str, base_url: str = "") -> tuple[str, str]:
    """(title, markdown). Only the main content when the page marks it."""
    lowered = html.lower()
    keep = ("main" if re.search(r"<main[\s>]", lowered)
            else "role=main" if re.search(r"role\s*=\s*[\"']main[\"']", lowered)
            else "article" if re.search(r"<article[\s>]", lowered) else None)
    p = _Markdown(base_url, keep)
    p.feed(html)
    body = _tidy("".join(p.out))
    if keep and len(body) < 200:            # a decorative main region: use the whole page instead
        title = p.title
        p = _Markdown(base_url, None)
        p.feed(html)
        body, p.title = _tidy("".join(p.out)), p.title or title
    return " ".join(p.title.split()), body


def html_to_text(html: str) -> str:
    """Plain text: the markdown with link targets and markup removed."""
    _, md = html_to_markdown(html)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"^#+ |^```$|`", "", md, flags=re.M)
    return _tidy(md)


# --------------------------------------------------------------------------- fetch

def _firecrawl_scrape(url: str, fmt: str) -> dict:
    k = key("firecrawl")
    r = requests.post(f"{FIRECRAWL}/scrape", timeout=60,
                      headers={"Authorization": f"Bearer {k}"} if k else {},
                      json={"url": url, "formats": ["html" if fmt == "html" else "markdown"],
                            "onlyMainContent": True})
    body = r.json() if r.content else {}
    if r.status_code != 200 or not body.get("success"):
        raise ValueError(f"firecrawl scrape failed ({r.status_code}): {body.get('error') or r.text[:200]}")
    data, meta = body.get("data") or {}, (body.get("data") or {}).get("metadata") or {}
    text = data.get("html" if fmt == "html" else "markdown") or ""
    if fmt == "text":
        text = html_to_text(f"<main>{text}</main>") if "<" in text else text
    return {"status": meta.get("statusCode", 200), "url": meta.get("sourceURL") or url,
            "title": meta.get("title") or "", "kind": "text/markdown",
            "source": f"firecrawl ({'keyed' if k else 'keyless'})", "text": text}


def _fallback(url: str, fmt: str, why: str, direct: dict | None = None) -> dict:
    """Firecrawl scrape after a direct fetch failed. If Firecrawl fails too, keep what the direct
    fetch did get (with both reasons), or raise with both reasons when there is nothing to keep."""
    try:
        return _firecrawl_scrape(url, fmt) | {"note": why}
    except (requests.RequestException, ValueError) as e:
        both = f"{why}; the firecrawl fallback failed too: {scrub(str(e))[:160]}"
        if direct is not None:
            return direct | {"note": both}
        raise ValueError(both)


def fetch(url: str, fmt: str = "markdown", fallback: str = "firecrawl") -> dict:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}, got '{fmt}'")
    guard(url)
    current, ua, why = url, BROWSER_UA, ""
    for _ in range(MAX_HOPS + 1):
        try:
            r = requests.get(current, timeout=20, stream=True, allow_redirects=False,
                             headers={"User-Agent": ua, "Accept": ACCEPT[fmt], "Accept-Language": "en-US,en;q=0.9"})
        except requests.RequestException as e:
            if fallback:
                return _fallback(current, fmt, f"direct fetch failed: {type(e).__name__}")
            raise
        with r:
            if 300 <= r.status_code < 400 and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                guard(current)                              # every hop, not just the first URL
                continue
            if r.status_code == 403 and r.headers.get("cf-mitigated") == "challenge" and ua == BROWSER_UA:
                ua = "mini-agent"                           # opencode retries a challenge as itself
                continue
            status, ctype = r.status_code, r.headers.get("content-type", "")
            raw = r.raw.read(MAX_BYTES + 1, decode_content=True)
        break
    else:
        raise ValueError(f"more than {MAX_HOPS} redirects from {url}")

    kind = ctype.split(";")[0].strip().lower()
    if status in BLOCKED:
        why = f"direct fetch was refused with HTTP {status}"
    elif kind == "application/pdf":
        why = "the page is a PDF"
    elif kind and not (kind.startswith("text/") or kind.endswith(("json", "xml", "javascript"))):
        return {"status": status, "url": current, "title": "", "kind": kind, "source": "direct",
                "text": f"({kind}, {len(raw)}{'+' if len(raw) > MAX_BYTES else ''} bytes - not text, not shown)"}
    if why:
        if not fallback:
            raise ValueError(why + " (no fallback configured)")
        return _fallback(current, fmt, why)

    try:
        body = raw.decode(charset(ctype), errors="replace")
    except LookupError:                                     # a charset name Python does not know
        body = raw.decode("utf-8", errors="replace")
    title, text = "", body
    if kind in ("text/html", "application/xhtml+xml") and fmt != "html":
        title, text = html_to_markdown(body, current)
        if fmt == "text":
            text = html_to_text(body)
    page = {"status": status, "url": current, "title": title, "kind": kind or "unknown", "source": "direct", "text": text}
    if kind == "text/html" and len(text) < 200 and len(body) > 5000 and fallback:   # a shell filled in by JavaScript
        return _fallback(current, fmt, "the page only renders with JavaScript", direct=page)
    return page


# --------------------------------------------------------------------------- search

def _firecrawl_search(query: str, n: int) -> dict:
    k = key("firecrawl")
    r = requests.post(f"{FIRECRAWL}/search", timeout=30, json={"query": query, "limit": n},
                      headers={"Authorization": f"Bearer {k}"} if k else {})
    body = r.json() if r.content else {}
    if r.status_code != 200 or not body.get("success"):
        raise ValueError(f"HTTP {r.status_code}: {body.get('error') or r.text[:200]}")
    data = body.get("data") or {}
    hits = data.get("web", []) if isinstance(data, dict) else data
    return {"provider": "firecrawl", "keyed": bool(k),
            "results": [{"title": h.get("title", ""), "url": h.get("url", ""),
                         "snippet": " ".join((h.get("description") or "").split())} for h in hits[:n]]}


def _exa_search(query: str, n: int) -> dict:
    """Exa's hosted MCP server, called as opencode does: one JSON-RPC tools/call over HTTP."""
    k = key("exa")
    headers = {"Accept": "application/json, text/event-stream"} | ({"x-api-key": k} if k else {})
    r = requests.post(EXA_MCP, timeout=30, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "web_search_exa",
                   "arguments": {"query": query, "type": "auto", "numResults": n, "livecrawl": "fallback"}}})
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}: {r.text[:200]}")
    for chunk in [r.text] + [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]:
        try:
            msg = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if "error" in msg:
            raise ValueError(str(msg["error"])[:200])
        text = next((c.get("text") for c in msg.get("result", {}).get("content", []) if c.get("text")), None)
        if text:
            return {"provider": "exa", "keyed": bool(k), "text": text.strip()}
    return {"provider": "exa", "keyed": bool(k), "text": ""}


SEARCH = {"firecrawl": _firecrawl_search, "exa": _exa_search}


def search(query: str, n: int = 5, providers=("firecrawl", "exa")) -> dict:
    """The first provider that answers with results; otherwise every provider's reason."""
    errors = []
    for name in providers:
        try:
            out = SEARCH[name](query, n)
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{name}: {scrub(str(e))}")
            continue
        if out.get("results") or out.get("text"):
            return out
        errors.append(f"{name}: no results")
    return {"provider": None, "errors": errors}
