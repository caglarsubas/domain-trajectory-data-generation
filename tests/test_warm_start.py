import io
import json
import socket
import zipfile

import httpx
import pytest

from app import runtime
from app.corpus_text import parse_file, sidecar
from app.documents import parse_bytes
from app.fetch import FetchError, Fetched, check_url, fetch_repository, safe_get
from app.retrieval import reference, split_passages
from sectors.registry import get_sector
from test_api import RecordingJudge, _auth, _project, _ready_key, _run


def pdf_with(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)


def docx_with(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Retail accounts", "version": "1.2"},
    "paths": {"/accounts": {"post": {"operationId": "openAccount", "summary": "Open a current account", "tags": ["accounts"]}},
              "/accounts/{id}/close": {"post": {"operationId": "closeAccount"}}},
    "components": {"schemas": {"Account": {}, "Party": {}}},
}


# Parsing


def test_pdf_word_and_html_are_read_as_text():
    pdf = parse_bytes(pdf_with("KYC passed before account opened"), "policy.pdf")
    assert pdf.parser == "PDF" and pdf.detail == "1 page" and "KYC passed before account opened" in pdf.text
    word = parse_bytes(docx_with(["Onboarding needs identity checks.", "Cards are issued after opening."]), "notes.docx")
    assert word.parser == "Word" and word.text == "Onboarding needs identity checks.\nCards are issued after opening."
    page = parse_bytes(
        b"<!doctype html><html><head><title>Savings guide</title><style>p{}</style><script>var x=1</script></head>"
        b"<body><h1>Open a savings account</h1><p>Fund it by transfer.</p></body></html>",
        "guide.html",
    )
    assert page.parser == "web page" and page.detail == "Savings guide"
    assert page.text.splitlines() == ["Savings guide", "", "Open a savings account", "Fund it by transfer."]
    assert "var x" not in page.text and "p{}" not in page.text


def test_api_definitions_are_summarized_before_their_text():
    spec = parse_bytes(json.dumps(OPENAPI).encode(), "openapi.json")
    assert spec.parser == "OpenAPI" and spec.detail == "2 operations"
    assert "- POST /accounts (openAccount): Open a current account [accounts]" in spec.text
    assert "Schemas: Account, Party" in spec.text
    events = parse_bytes(b"asyncapi: '2.6.0'\ninfo: {title: Ledger events, version: '1'}\nchannels:\n  account.opened:\n    subscribe: {}\n", "asyncapi.yaml")
    assert events.parser == "AsyncAPI" and "- channel account.opened (subscribe)" in events.text


def test_unreadable_files_say_why():
    assert "no text layer" in parse_bytes(pdf_with(""), "scan.pdf").reason
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", "a,b")
    assert "Zip archives" in parse_bytes(buffer.getvalue(), "data.zip").reason
    assert "Binary" in parse_bytes(b"\x00\x01\x02" * 100, "blob.bin").reason


def test_a_file_is_parsed_once_and_read_from_its_cache(tmp_path, monkeypatch):
    path = tmp_path / "abc-notes.md"
    path.write_text("# Deposits\nCustomers fund accounts by transfer.")
    first = parse_file(str(path))
    assert first.parser == "Markdown" and sidecar(path).is_file()
    import app.corpus_text as corpus_text

    corpus_text._parse_cached.cache_clear()

    def refuse(*args, **kwargs):
        raise AssertionError("parsed again")

    monkeypatch.setattr(corpus_text, "parse_bytes", refuse)
    assert parse_file(str(path)).text == first.text


def test_an_uploaded_file_name_cannot_leave_the_upload_directory(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    headers = _auth(client, "upload-name@example.com", "password-123")
    project_id = _project(client, headers)
    response = client.post(
        f"/projects/{project_id}/corpus",
        headers=headers,
        data={"kind": "paper"},
        files={"upload": ("../../escape.pdf", pdf_with("Account opened after KYC"), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert not (tmp_path / "escape.pdf").exists()
    stored = list((tmp_path / "uploads").glob("*escape.pdf"))
    assert len(stored) == 1 and stored[0].parent == tmp_path / "uploads"
    corpus = client.get("/projects", headers=headers).json()["data"][0]["corpus"][0]
    assert corpus["readable"] is True and corpus["parser"] == "PDF" and corpus["name"] == "../../escape.pdf"


# Fetching


def resolver_for(table: dict[str, str]):
    def resolve(host, port, type=None):
        if host not in table:
            raise socket.gaierror("unknown")
        family = socket.AF_INET6 if ":" in table[host] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (table[host], port))]

    return resolve


@pytest.mark.parametrize(
    "url, table",
    [
        ("file:///etc/passwd", {}),
        ("http://localhost/admin", {"localhost": "127.0.0.1"}),
        ("http://intranet.example/", {"intranet.example": "10.1.2.3"}),
        ("http://metadata.example/latest", {"metadata.example": "169.254.169.254"}),
        ("http://v6.example/", {"v6.example": "::1"}),
        ("http://mapped.example/", {"mapped.example": "::ffff:127.0.0.1"}),
        ("http://carrier.example/", {"carrier.example": "100.64.0.1"}),
        ("https://user:secret@public.example/", {"public.example": "93.184.216.34"}),
    ],
)
def test_links_inside_a_private_network_are_refused(url, table):
    with pytest.raises(FetchError):
        check_url(url, resolver_for(table))


def test_a_public_link_is_fetched_but_a_redirect_inside_is_not():
    resolver = resolver_for({"public.example": "93.184.216.34", "internal.example": "192.168.1.9"})

    def handler(request):
        if request.url.path == "/go-inside":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2000)
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    assert safe_get("https://public.example/page", transport=transport, resolver=resolver).body == b"<p>ok</p>"
    with pytest.raises(FetchError, match="private network"):
        safe_get("https://public.example/go-inside", transport=transport, resolver=resolver)
    with pytest.raises(FetchError, match="larger than"):
        safe_get("https://public.example/big", transport=transport, resolver=resolver, max_bytes=1000)
    with pytest.raises(FetchError, match="answered 404"):
        safe_get("https://public.example/missing", transport=transport, resolver=resolver)


def test_a_github_repository_is_read_through_its_readme_docs_and_api_definitions():
    resolver = resolver_for({"api.github.com": "140.82.112.6", "raw.githubusercontent.com": "185.199.108.133"})
    import yaml

    files = {
        "README.md": "# Core banking\nAccounts open after KYC passes.",
        "docs/cards.md": "Cards are issued, then activated.",
        "api/openapi.yaml": yaml.safe_dump(OPENAPI),
        "src/main.py": "print('ignored')",
    }

    def handler(request):
        path = request.url.path
        if request.url.host == "api.github.com" and path == "/repos/acme/core":
            return httpx.Response(200, json={"default_branch": "trunk", "description": "A core banking sample"})
        if request.url.host == "api.github.com" and path == "/repos/acme/core/git/trees/trunk":
            return httpx.Response(200, json={"tree": [{"path": name, "type": "blob", "size": len(body)} for name, body in files.items()]})
        if request.url.host == "raw.githubusercontent.com":
            name = path.removeprefix("/acme/core/trunk/")
            return httpx.Response(200, text=files[name]) if name in files else httpx.Response(404)
        return httpx.Response(404)

    found = fetch_repository("acme", "core", transport=httpx.MockTransport(handler), resolver=resolver)
    text = found.body.decode()
    assert "## README: README.md" in text and "Accounts open after KYC passes." in text
    assert "## Documentation: docs/cards.md" in text
    assert "- POST /accounts (openAccount): Open a current account [accounts]" in text
    assert "print('ignored')" not in text
    assert found.detail == "README, 1 documents, 1 API definitions"
    assert len(found.sources) == 3 and all(source.startswith("https://raw.githubusercontent.com/acme/core/trunk/") for source in found.sources)


class PageFetcher:
    def fetch(self, url, kind):
        return Fetched(url, b"<html><head><title>KYC rules</title></head><body><p>Identity is verified before an account is opened.</p></body></html>", "text/html; charset=utf-8")


def test_a_link_is_fetched_by_a_job_and_read_like_an_upload(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "fetch-link@example.com", "password-123")
    project_id = _project(client, headers)
    runtime.fetcher = PageFetcher()
    body = client.post(f"/projects/{project_id}/corpus/link", headers=headers, json={"kind": "paper", "name": "KYC page", "uri": "https://bank.example/kyc"}).json()
    assert body["job"]["status"] == "succeeded"
    assert body["readable"] is True and body["parser"] == "web page" and body["parse_detail"] == "KYC rules"
    assert body["ingest"]["status"] == "fetched" and body["ingest"]["bytes"] > 50

    from conftest import OfflineFetcher

    runtime.fetcher = OfflineFetcher()
    failed = client.post(f"/projects/{project_id}/corpus/link", headers=headers, json={"kind": "paper", "name": "Gone", "uri": "https://bank.example/gone"})
    assert failed.status_code == 200
    failed = failed.json()
    assert failed["readable"] is False and failed["ingest"]["status"] == "failed"
    assert "could not be fetched" in failed["unreadable_reason"] and failed["job"]["status"] == "failed"


# Retrieval


class Doc:
    def __init__(self, name, path, index):
        self.id, self.kind, self.name, self.uri, self.storage_path, self.ingest = f"d{index}", "paper", name, None, path, None
        self.created_at = index


def test_the_judge_reads_the_passages_about_the_study_not_the_first_characters(tmp_path):
    noise = "\n\n".join(f"Paragraph {n} about the weather and the history of the town square." for n in range(40))
    relevant = "Before an account is opened the bank verifies identity; KYC passed is required, then the account is funded."
    first = tmp_path / "a-town.md"
    first.write_text(noise)
    second = tmp_path / "b-kyc.md"
    second.write_text(noise + "\n\n" + relevant + "\n\n" + noise)
    text, chosen = reference([Doc("town.md", str(first), 1), Doc("kyc.md", str(second), 2)], get_sector("banking"), ["onboarding_and_kyc", "deposits"], budget=600)
    assert text.startswith("[kyc.md] ") and relevant in text
    assert len(text) <= 700 and chosen[0]["source"] == "kyc.md" and chosen[0]["score"] > 0
    fallback, picked = reference([Doc("town.md", str(first), 1)], get_sector("banking"), ["onboarding_and_kyc"], budget=400)
    assert fallback.startswith("[town.md] Paragraph 0") and picked[0]["score"] == 0.0


def test_passages_follow_paragraphs_and_stay_short():
    text = "\n\n".join(["Short one."] * 3 + ["A long paragraph. " * 100])
    passages = split_passages("doc", text, size=300)
    assert passages[0].text == "Short one.\nShort one.\nShort one."
    assert all(len(item.text) <= 300 for item in passages)


def test_a_judged_warm_run_briefs_the_judge_with_retrieved_passages(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers = _auth(client, "reference-judge@example.com", "password-123")
    project_id = _project(client, headers)
    runtime.fetcher = PageFetcher()
    client.post(f"/projects/{project_id}/corpus/link", headers=headers, json={"kind": "paper", "name": "KYC page", "uri": "https://bank.example/kyc"})
    credential_id = _ready_key(client, headers)
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=4).json()
    runtime.judge = RecordingJudge()
    cycle = client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={}).json()["cycles"][0]
    brief = next(call["prompt"] for call in runtime.judge.calls if call["rubric"] == "helpfulness")
    assert "Warm-start reference passages:" in brief and "[KYC page]" in brief and "Identity is verified" in brief
    assert cycle["reference"] and cycle["reference"][0]["source"] == "KYC page"
