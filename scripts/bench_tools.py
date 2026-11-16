"""Article tools for the models in model_bench_tui.py.

Models can list and read files in an articles folder and fetch web pages.
Fetches are memoized for the whole run, so every model asking for a URL gets
exactly what the first one got (and concurrent requests share one fetch):
a bench compares the models, not whatever the site served each time.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urlparse

import httpx

MAX_ARTICLE_CHARS = 40_000  # per tool result; longer text is cut with a note
FETCH_TIMEOUT = 20.0
# Plenty of sites refuse obvious bots; this still says what it is.
USER_AGENT = "Mozilla/5.0 (compatible; model-bench/1.0)"
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".json", ".csv", ""}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_articles",
            "description": "List the articles available in the local articles folder.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_article",
            "description": "Read an article from the local articles folder, as text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "file name from list_articles (extension optional)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a web page and return its main text (HTML is converted to plain text).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "http or https URL"}},
                "required": ["url"],
            },
        },
    },
]


@dataclass
class ToolResult:
    content: str  # goes back to the model
    note: str  # one line for the chat column
    error: bool = False
    source: tuple[str, str] | None = None  # (name, text) the model read, for the judge


class PageText(HTMLParser):
    """Readable text from an HTML page: skips scripts, navigation and other
    chrome, keeps block structure as line breaks, and prefers what's inside
    <article> (or <main>) when the page has a substantial one."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form",
            "template", "iframe", "button", "select", "figure"}
    BLOCK = {"p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table",
             "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "dd", "dt"}
    VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "col", "embed"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._main = 0
        self.all: list[str] = []
        self.main: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.VOID:
            if tag in ("br", "hr"):
                self._emit("\n")
            return
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
        if tag in ("article", "main"):
            self._main += 1
        if tag in self.BLOCK:
            self._emit("\n")
        if tag in ("h1", "h2", "h3") and not self._skip:
            self._emit("#" * int(tag[1]) + " ")
        elif tag == "li" and not self._skip:
            self._emit("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self._emit("\n")
        if tag in ("article", "main") and self._main:
            self._main -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._emit(data)

    def _emit(self, text: str) -> None:
        self.all.append(text)
        if self._main:
            self.main.append(text)

    @staticmethod
    def _tidy(parts: list[str]) -> str:
        lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parts).split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    def text(self) -> str:
        main = self._tidy(self.main)
        return main if len(main) >= 500 else self._tidy(self.all)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page."""
    parser = PageText()
    parser.feed(html)
    parser.close()
    return " ".join(parser.title.split()), parser.text()


def cut(text: str) -> str:
    if len(text) <= MAX_ARTICLE_CHARS:
        return text
    return text[:MAX_ARTICLE_CHARS] + f"\n\n[... cut: showing {MAX_ARTICLE_CHARS:,} of {len(text):,} characters]"


class ArticleTools:
    def __init__(self, articles_dir: Path, client: httpx.AsyncClient) -> None:
        self.articles_dir = articles_dir
        self.client = client
        self._fetches: dict[str, asyncio.Task[tuple[str, bool]]] = {}  # url -> (text, ok)

    async def run(self, name: str, arguments: str) -> ToolResult:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise TypeError("arguments must be an object")
            if name == "list_articles":
                return self.list_articles()
            if name == "read_article":
                return self.read_article(str(args["name"]))
            if name == "fetch_url":
                return await self.fetch_url(str(args["url"]))
        except (ValueError, KeyError, TypeError) as exc:
            msg = f"Error: bad arguments for {name}: {exc}"
            return ToolResult(msg, f"{name}: bad arguments ({exc})", error=True)
        return ToolResult(f"Error: unknown tool {name}", f"Unknown tool: {name}", error=True)

    # -- local articles -------------------------------------------------

    def _files(self) -> list[Path]:
        if not self.articles_dir.is_dir():
            return []
        return sorted(p for p in self.articles_dir.rglob("*")
                      if p.is_file() and not any(part.startswith(".") for part in p.relative_to(self.articles_dir).parts)
                      and p.suffix.lower() in TEXT_SUFFIXES)

    def list_articles(self) -> ToolResult:
        files = self._files()
        if not files:
            where = self.articles_dir if self.articles_dir.is_dir() else f"{self.articles_dir} (missing)"
            return ToolResult(f"No articles in {where}.", "list_articles: none found")
        lines = [f"{p.relative_to(self.articles_dir)}  ({p.stat().st_size:,} bytes)" for p in files]
        return ToolResult("\n".join(lines), f"list_articles: {len(files)} found")

    def read_article(self, name: str) -> ToolResult:
        root = self.articles_dir.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            return ToolResult("Error: articles must be inside the articles folder.",
                              f"read_article {name}: outside the folder", error=True)
        if not path.is_file():
            # Forgive a missing extension or a bare file name in a subfolder.
            want = name.lower()
            matches = [p for p in self._files()
                       if p.stem.lower() == want or p.name.lower() == want
                       or str(p.relative_to(self.articles_dir).with_suffix("")).lower() == want]
            if len(matches) != 1:
                why = "is ambiguous" if matches else "not found"
                return ToolResult(f"Error: article {name!r} {why}. Use list_articles to see the names.",
                                  f"read_article {name}: {why}", error=True)
            path = matches[0].resolve()
        try:
            raw = path.read_text(errors="replace")
        except OSError as exc:
            return ToolResult(f"Error: couldn't read {name}: {exc}", f"read_article {name}: {exc}", error=True)
        rel = str(path.relative_to(root))
        if path.suffix.lower() in (".html", ".htm"):
            title, text = html_to_text(raw)
            text = f"Title: {title}\n\n{text}" if title else text
        else:
            text = raw
        return ToolResult(cut(text), f"read_article {rel}: {len(text):,} chars", source=(rel, text))

    # -- web -------------------------------------------------------------

    async def fetch_url(self, url: str) -> ToolResult:
        url = urldefrag(url.strip())[0]
        if urlparse(url).scheme not in ("http", "https"):
            return ToolResult("Error: only http and https URLs can be fetched.",
                              f"fetch_url {url}: not http(s)", error=True)
        task = self._fetches.get(url)
        cached = task is not None
        if task is None:
            # A task, not a coroutine: a model cancelled mid-fetch doesn't
            # cancel it for the others waiting on the same URL.
            task = self._fetches[url] = asyncio.create_task(self._fetch(url))
        text, ok = await asyncio.shield(task)
        tag = " (cached)" if cached else ""
        if not ok:
            return ToolResult(text, f"fetch_url {url}: {text.removeprefix('Error: ')}{tag}", error=True)
        return ToolResult(cut(text), f"fetch_url {url}: {len(text):,} chars{tag}", source=(url, text))

    async def _fetch(self, url: str) -> tuple[str, bool]:
        """Fetch once. Failures are remembered too, so every model sees the same."""
        try:
            resp = await self.client.get(url, follow_redirects=True, timeout=FETCH_TIMEOUT,
                                         headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            return f"Error: {exc or type(exc).__name__}", False
        if resp.status_code != 200:
            return f"Error: HTTP {resp.status_code}", False
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype in ("text/html", "application/xhtml+xml") or (not ctype and "<html" in resp.text[:2000].lower()):
            title, text = html_to_text(resp.text)
        elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
            title, text = "", resp.text
        else:
            return f"Error: can't read {ctype or 'unknown'} content", False
        if not text.strip():
            return "Error: the page has no readable text (it may need JavaScript)", False
        head = f"Title: {title}\n" if title else ""
        return f"{head}URL: {resp.url}\n\n{text}", True
