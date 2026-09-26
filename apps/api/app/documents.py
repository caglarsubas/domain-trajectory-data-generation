"""Turn warm-start files into text: PDF, Word, HTML, Markdown and plain text, and API definitions.

Each parser returns the text and a short account of what it read, or the reason it could not.
Nothing here touches the network; fetching lives in app.fetch.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree

# Characters kept per document. Steering reads all of it; the judge reads retrieved passages.
MAX_TEXT = 400_000
MAX_PDF_PAGES = 300
MAX_DOCX_XML = 50_000_000
# Bumped when a parser changes, so cached text is read again.
PARSER_VERSION = 2


@dataclass(frozen=True)
class Parsed:
    text: str
    parser: str
    detail: str
    reason: str | None = None

    @property
    def readable(self) -> bool:
        return bool(self.text.strip())


def parse_bytes(raw: bytes, name: str = "", content_type: str = "") -> Parsed:
    lower, ctype = name.lower(), content_type.lower()
    if raw.startswith(b"%PDF"):
        return _pdf(raw)
    if raw.startswith(b"PK\x03\x04"):
        return _docx(raw)
    if b"\x00" in raw[:1024]:
        return Parsed("", "binary", "", "Binary file; no text was read.")
    text = raw.decode("utf-8", errors="replace")
    if text.count("�") > max(20, len(text) // 20):
        return Parsed("", "text", "", "The file is not UTF-8 text.")
    head = text.lstrip()[:300].lower()
    if "html" in ctype or lower.endswith((".html", ".htm")) or head.startswith(("<!doctype html", "<html")):
        return _html(text)
    if "json" in ctype or lower.endswith(".json"):
        return _structured(text, json.loads, "JSON")
    if "yaml" in ctype or lower.endswith((".yaml", ".yml")):
        import yaml

        return _structured(text, yaml.safe_load, "YAML")
    parser = "Markdown" if lower.endswith((".md", ".mdx", ".markdown")) else "text"
    return Parsed(text[:MAX_TEXT], parser, f"{len(text):,} characters", None)


def _pdf(raw: bytes) -> Parsed:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            return Parsed("", "PDF", "", "The PDF is encrypted.")
        pages = reader.pages
        texts = []
        for page in list(pages)[:MAX_PDF_PAGES]:
            texts.append(page.extract_text() or "")
            if sum(len(item) for item in texts) > MAX_TEXT:
                break
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        return Parsed("", "PDF", "", "The PDF could not be read.")
    text = "\n\n".join(item.strip() for item in texts if item.strip())[:MAX_TEXT]
    detail = f"{len(pages)} {'page' if len(pages) == 1 else 'pages'}" + (f", first {MAX_PDF_PAGES} read" if len(pages) > MAX_PDF_PAGES else "")
    if not text.strip():
        return Parsed("", "PDF", detail, "The PDF has no text layer; scanned pages would need OCR, which is not done.")
    return Parsed(text, "PDF", detail)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(raw: bytes) -> Parsed:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        info = archive.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        return Parsed("", "zip", "", "Zip archives other than Word documents are not read.")
    if info.file_size > MAX_DOCX_XML:
        return Parsed("", "Word", "", "The Word document is too large to read.")
    xml = archive.read(info)
    if b"<!DOCTYPE" in xml[:2000]:
        return Parsed("", "Word", "", "The Word document declares a DTD, which is not read.")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return Parsed("", "Word", "", "The Word document could not be read.")
    paragraphs = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in {f"{_W}br", f"{_W}cr"}:
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    text = "\n".join(paragraphs)[:MAX_TEXT]
    if not text:
        return Parsed("", "Word", "", "The Word document has no text.")
    return Parsed(text, "Word", f"{len(paragraphs):,} paragraphs")


class _Text(HTMLParser):
    # Navigation, forms, and page chrome carry no domain knowledge.
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "aside", "form", "button", "select"}
    BLOCK = {"p", "div", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "br", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        # Text inside <main> or <article>, which is preferred when a page marks its content.
        self.main: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._main = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"main", "article"} or dict(attrs).get("role") == "main":
            self._main += 1
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self._add("\n")
        if tag in {"main", "article"} and self._main:
            self._main -= 1

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self._add(data)

    def _add(self, text: str) -> None:
        self.parts.append(text)
        if self._main:
            self.main.append(text)


def _html(text: str) -> Parsed:
    parser = _Text()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup reads as whatever was collected
        pass
    main = "".join(parser.main)
    source = main if len(main.strip()) > 200 else "".join(parser.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in source.split("\n")]
    body = "\n".join(line for line in lines if line)
    title = re.sub(r"\s+", " ", parser.title).strip()
    joined = (f"{title}\n\n{body}" if title else body)[:MAX_TEXT]
    if not joined.strip():
        return Parsed("", "web page", "", "The page has no readable text.")
    return Parsed(joined, "web page", title or f"{len(body):,} characters")


def _structured(text: str, load, label: str) -> Parsed:
    try:
        data = load(text)
    except Exception:  # noqa: BLE001 - unparseable data is still readable as text
        return Parsed(text[:MAX_TEXT], label, f"{len(text):,} characters, not parsed")
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
        summary, count = api_summary(data)
        return Parsed((summary + "\n\n" + text)[:MAX_TEXT], "OpenAPI", f"{count} operations")
    if isinstance(data, dict) and "asyncapi" in data:
        summary, count = asyncapi_summary(data)
        return Parsed((summary + "\n\n" + text)[:MAX_TEXT], "AsyncAPI", f"{count} channels")
    return Parsed(text[:MAX_TEXT], label, f"{len(text):,} characters")


METHODS = ("get", "post", "put", "patch", "delete")
OPERATION_LINE = re.compile(r"^- (GET|POST|PUT|PATCH|DELETE) (\S+)(?: \(([^)]+)\))?(?:: (.*?))?(?: \[[^\]]*\])?$", re.MULTILINE)


def operations_from_text(text: str) -> list[dict]:
    """The operations an API summary lists, as written by `api_summary` for uploads and repositories alike."""
    return [
        {"method": method, "path": path, "operationId": name, "summary": summary}
        for method, path, name, summary in OPERATION_LINE.findall(text)
    ]


def api_summary(spec: dict) -> tuple[str, int]:
    """The operations of an OpenAPI definition, one line each, ahead of the raw text."""
    info = spec.get("info") or {}
    lines = [f"API definition: {info.get('title', 'untitled')} {info.get('version', '')}".strip()]
    count = 0
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            count += 1
            name = operation.get("operationId") or ""
            summary = operation.get("summary") or operation.get("description") or ""
            tags = ", ".join(operation.get("tags") or [])
            lines.append(f"- {method.upper()} {path}" + (f" ({name})" if name else "") + (f": {summary}" if summary else "") + (f" [{tags}]" if tags else ""))
    schemas = list(((spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}).keys())
    if schemas:
        lines.append("Schemas: " + ", ".join(schemas[:80]))
    return "\n".join(lines), count


def asyncapi_summary(spec: dict) -> tuple[str, int]:
    info = spec.get("info") or {}
    lines = [f"Event API definition: {info.get('title', 'untitled')} {info.get('version', '')}".strip()]
    channels = spec.get("channels") or {}
    for name, channel in channels.items():
        if not isinstance(channel, dict):
            continue
        actions = [key for key in ("publish", "subscribe") if key in channel]
        described = channel.get("description") or ""
        lines.append(f"- channel {name}" + (f" ({', '.join(actions)})" if actions else "") + (f": {described}" if described else ""))
    return "\n".join(lines), len(channels)
