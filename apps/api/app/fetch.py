"""Fetch warm-start links without reaching inside the network the platform runs in.

Only http and https are fetched. Every address a host name resolves to must be a public one, before the
request and again for each redirect, and the address actually connected to is checked before the body is
read, which closes the gap a name that resolves differently the second time would open. Bodies stop at a
size limit. A GitHub repository link is read through its README, documentation, and API definitions.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.documents import api_summary, asyncapi_summary

MAX_BYTES = 5_000_000
MAX_REDIRECTS = 5
TIMEOUT = 20.0
# Sites such as Wikipedia refuse clients that do not say who they are and how to reach them.
USER_AGENT = os.environ.get(
    "FETCH_USER_AGENT", "trajectory-studio/0.1 (warm-start fetcher; +https://github.com/caglarsubas/domain-trajectory-data-generation)"
)
ACCEPT = "text/html, text/markdown, text/plain, application/pdf, application/json, application/yaml, application/vnd.openxmlformats-officedocument.wordprocessingml.document;q=0.9, */*;q=0.1"


class FetchError(Exception):
    """A link that could not be fetched, with a reason safe to show."""


@dataclass
class Fetched:
    url: str
    body: bytes
    content_type: str
    sources: list[str] = field(default_factory=list)
    parser_hint: str = ""
    detail: str = ""


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver=socket.getaddrinfo) -> tuple[str, int]:
    """Refuse anything but a public http or https address. Returns the host and port."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise FetchError("only http and https links are fetched")
    if parts.username or parts.password:
        raise FetchError("links with a user name or password are not fetched")
    host = parts.hostname
    if not host:
        raise FetchError("the link has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise FetchError(f"{host} could not be resolved") from exc
    addresses = {answer[4][0] for answer in answers}
    if not addresses:
        raise FetchError(f"{host} could not be resolved")
    if not all(_public(address) for address in addresses):
        raise FetchError(f"{host} points inside a private network, which is not fetched")
    return host, port


def safe_get(url: str, *, transport: httpx.BaseTransport | None = None, resolver=socket.getaddrinfo, max_bytes: int = MAX_BYTES, headers: dict | None = None) -> Fetched:
    current = url
    with httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, resolver)
            request_headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT, **(headers or {})}
            try:
                with client.stream("GET", current, headers=request_headers) as response:
                    stream = response.extensions.get("network_stream")
                    peer = stream.get_extra_info("server_addr") if stream is not None else None
                    if peer and not _public(str(peer[0])):
                        raise FetchError("the link resolved to a private address when fetched")
                    if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"the server answered {response.status_code}")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError(f"the file is larger than {max_bytes // 1_000_000} MB")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError(f"the file is larger than {max_bytes // 1_000_000} MB")
                    return Fetched(str(response.url), bytes(body), response.headers.get("content-type", ""))
            except httpx.TimeoutException as exc:
                raise FetchError("the server took too long to answer") from exc
            except httpx.HTTPError as exc:
                raise FetchError("the server could not be reached") from exc
    raise FetchError("the link redirected too many times")


def safe_download(url: str, destination, *, max_bytes: int, transport: httpx.BaseTransport | None = None, resolver=socket.getaddrinfo) -> tuple[str, str, int, str]:
    """Stream a public file to disk under the same guard as a page. Returns final address, content type, size, and SHA-256."""
    import hashlib

    current = url
    with httpx.Client(transport=transport, timeout=httpx.Timeout(TIMEOUT, read=120.0), follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current, resolver)
            try:
                with client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as response:
                    stream = response.extensions.get("network_stream")
                    peer = stream.get_extra_info("server_addr") if stream is not None else None
                    if peer and not _public(str(peer[0])):
                        raise FetchError("the link resolved to a private address when fetched")
                    if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"the server answered {response.status_code}")
                    digest, size = hashlib.sha256(), 0
                    with open(destination, "wb") as handle:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise FetchError(f"the file is larger than {max_bytes // 1_000_000} MB")
                            digest.update(chunk)
                            handle.write(chunk)
                    return str(response.url), response.headers.get("content-type", ""), size, digest.hexdigest()
            except httpx.TimeoutException as exc:
                raise FetchError("the server took too long to answer") from exc
            except httpx.HTTPError as exc:
                raise FetchError("the server could not be reached") from exc
    raise FetchError("the link redirected too many times")


GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$")
DOC_FILE = re.compile(r"^(docs?|documentation)/.+\.(md|mdx|markdown|rst|txt)$", re.IGNORECASE)
API_FILE = re.compile(r"(^|/)(openapi|swagger|asyncapi)[^/]*\.(json|ya?ml)$", re.IGNORECASE)
MAX_DOCS, MAX_APIS, MAX_FILE = 20, 10, 300_000


def github_repository(url: str) -> tuple[str, str] | None:
    match = GITHUB.match(url.strip())
    return (match.group(1), match.group(2)) if match else None


def fetch_repository(owner: str, repo: str, *, transport=None, resolver=socket.getaddrinfo) -> Fetched:
    """A repository as one Markdown text: its README, documentation files, and API definitions, with their addresses."""
    import json

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    api_headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}

    def api(path: str) -> dict:
        found = safe_get(f"https://api.github.com{path}", transport=transport, resolver=resolver, headers=api_headers)
        return json.loads(found.body)

    try:
        meta = api(f"/repos/{owner}/{repo}")
        branch = meta.get("default_branch") or "main"
        tree = api(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1").get("tree", [])
    except FetchError as exc:
        raise FetchError(f"GitHub could not list {owner}/{repo}: {exc}") from exc
    except ValueError as exc:
        raise FetchError("GitHub returned a response that could not be read") from exc
    blobs = [item for item in tree if item.get("type") == "blob" and (item.get("size") or 0) <= MAX_FILE]
    readme = next((item["path"] for item in blobs if re.match(r"^readme(\.(md|markdown|rst|txt))?$", item["path"], re.IGNORECASE)), None)
    docs = sorted(item["path"] for item in blobs if DOC_FILE.match(item["path"]))[:MAX_DOCS]
    apis = sorted(item["path"] for item in blobs if API_FILE.search(item["path"]))[:MAX_APIS]
    sections = [f"# Repository {owner}/{repo}", meta.get("description") or ""]
    sources = []
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
    for label, paths in (("README", [readme] if readme else []), ("Documentation", docs), ("API definition", apis)):
        for path in paths:
            try:
                found = safe_get(raw + path, transport=transport, resolver=resolver, max_bytes=MAX_FILE)
            except FetchError:
                continue
            text = found.body.decode("utf-8", errors="replace")
            if label == "API definition":
                text = _api_text(path, text)
            sections.append(f"## {label}: {path}\n\n{text}")
            sources.append(raw + path)
    if len(sections) <= 2:
        raise FetchError(f"{owner}/{repo} has no README, documentation, or API definition to read")
    detail = f"README{'' if readme else ' missing'}, {len(docs)} documents, {len(apis)} API definitions"
    body = "\n\n".join(item for item in sections if item) + "\n\nSources:\n" + "\n".join(sources)
    return Fetched(f"https://github.com/{owner}/{repo}", body.encode(), "text/markdown", sources, "GitHub repository", detail)


def _api_text(path: str, text: str) -> str:
    import json

    import yaml

    try:
        data = json.loads(text) if path.lower().endswith(".json") else yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - an unparseable definition is kept as text
        return text
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
        return api_summary(data)[0]
    if isinstance(data, dict) and "asyncapi" in data:
        return asyncapi_summary(data)[0]
    return text


def fetch_link(url: str, kind: str, *, transport=None, resolver=socket.getaddrinfo) -> Fetched:
    repository = github_repository(url) if kind == "repo" else None
    if repository:
        return fetch_repository(*repository, transport=transport, resolver=resolver)
    return safe_get(url, transport=transport, resolver=resolver)
