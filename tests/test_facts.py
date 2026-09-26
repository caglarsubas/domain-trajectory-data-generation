import json

import pytest

from app import runtime
from sectors.facts import extract_facts, steering_from_facts
from sectors.registry import get_sector
from test_api import RecordingJudge, _auth, _project, _ready_key, _run

BANKING = get_sector("banking")
NAMESPACE = tuple(BANKING.event_namespace)

POLICY = (
    "Customers open a current account in GBP through the mobile app. "
    "Prices are also shown in £ on posters. "
    "The branch is closed on Sundays. "
    "After kyc.passed the account is funded. "
    "There was no kyc.failed case this year. "
    "A savings product is mentioned in the brochure."
)


def _facts(text, name="policy.md"):
    return {fact.key: fact for fact in extract_facts([(name, text)], BANKING.vocabulary, NAMESPACE)}


def test_facts_are_explicit_when_stated_and_implied_when_only_pointed_at():
    facts = _facts(POLICY)
    assert facts["currency:GBP"].support == "explicit" and facts["currency:GBP"].mentions == 2
    assert facts["currency:GBP"].evidence[0]["sentence"].startswith("Customers open a current account in GBP")
    assert facts["channel:mobile"].support == "explicit"
    assert facts["channel:branch"].support == "implied"
    assert facts["product:current"].support == "explicit"
    assert facts["product:savings"].support == "implied"
    assert facts["event:kyc.passed"].support == "explicit"
    assert facts["negated_event:kyc.failed"].support == "explicit"
    assert facts["channel:branch"].confidence < facts["channel:mobile"].confidence
    symbol_only = _facts("Fees are listed in £ and pounds.")
    assert symbol_only["currency:GBP"].support == "implied" and symbol_only["currency:GBP"].mentions == 2


def test_a_named_event_is_not_also_a_negated_one():
    facts = _facts("There was no kyc.failed case. Later kyc.failed happened once.")
    assert "event:kyc.failed" in facts and "negated_event:kyc.failed" not in facts


def test_steering_takes_explicit_facts_and_accepted_implied_ones_only():
    facts = list(_facts(POLICY).values())
    plain = steering_from_facts(facts, {}, NAMESPACE)
    assert plain.currency == "GBP" and plain.channel == "mobile" and plain.products == ("current",)
    assert plain.events == ("kyc.passed",) and plain.negated == ("kyc.failed",)
    reviewed = steering_from_facts(facts, {"product:savings": "accepted", "channel:mobile": "rejected"}, NAMESPACE)
    assert set(reviewed.products) == {"current", "savings"} and reviewed.channel is None


def _study_with(client, email, text, name="policy.md"):
    headers = _auth(client, email, "password-123")
    project_id = _project(client, headers)
    uploaded = client.post(f"/projects/{project_id}/corpus", headers=headers, data={"kind": "paper"}, files={"upload": (name, text.encode(), "text/markdown")})
    assert uploaded.status_code == 200, uploaded.text
    return headers, project_id, _ready_key(client, headers)


def test_a_study_lists_its_facts_and_reviews_move_the_implied_ones(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id, _ = _study_with(client, "facts-api@example.com", POLICY)
    report = client.get(f"/projects/{project_id}/facts", headers=headers, params={"sub_domains": "onboarding_and_kyc,complaints", "language": "en"}).json()
    explicit = {row["key"] for row in report["explicit"]}
    implied = {row["key"]: row for row in report["implied"]}
    assert {"currency:GBP", "channel:mobile", "product:current", "event:kyc.passed"} <= explicit
    assert "product:savings" in implied and implied["product:savings"]["steers"] is False
    assert report["counts"]["awaiting_review"] == len(implied)
    statements = [item["statement"] for item in report["unsupported"]]
    assert any("complaints" in item for item in statements)
    assert not any(item.startswith("No document names a currency") for item in statements)

    accepted = client.post(f"/projects/{project_id}/facts/review", headers=headers, json={"key": "product:savings", "decision": "accepted"})
    assert accepted.status_code == 200 and accepted.json()["review"] == "accepted"
    client.post(f"/projects/{project_id}/facts/review", headers=headers, json={"key": "currency:GBP", "decision": "rejected"})
    after = client.get(f"/projects/{project_id}/facts", headers=headers, params={"language": "tr"}).json()
    rows = {row["key"]: row for row in after["explicit"] + after["implied"]}
    assert rows["product:savings"]["steers"] is True and rows["currency:GBP"]["steers"] is False
    assert any(item["statement"] == "No document names a currency; amounts use TRY from the language (tr)." for item in after["unsupported"])
    cleared = client.post(f"/projects/{project_id}/facts/review", headers=headers, json={"key": "currency:GBP", "decision": "clear"}).json()
    assert cleared["review"] is None
    assert client.post(f"/projects/{project_id}/facts/review", headers=headers, json={"key": "x:y", "decision": "maybe"}).status_code == 422


def _currencies(run):
    return {event["currency"] for event in run["bundle"]["events"] if event.get("currency")}


def test_an_implied_currency_steers_only_once_accepted(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id, credential_id = _study_with(client, "facts-steer@example.com", "Deposits and fees are listed in dollars on every statement.")
    options = dict(target_trajectory_count=12, event_budget=None, language="tr", sub_domains=["onboarding_and_kyc", "deposits"])
    before = _run(client, headers, project_id, credential_id, **options).json()
    assert _currencies(before) == {"TRY"}
    assert before["generation"]["steering"]["facts"]["awaiting_review"] >= 1
    client.post(f"/projects/{project_id}/facts/review", headers=headers, json={"key": "currency:USD", "decision": "accepted"})
    after = _run(client, headers, project_id, credential_id, **options).json()
    assert _currencies(after) == {"USD"}
    assert after["generation"]["steering"]["currency"] == "USD" and after["generation"]["steering"]["facts"]["accepted"] == 1


def test_a_jurisdiction_sets_currency_product_names_and_kyc_rules(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    headers, project_id, credential_id = _study_with(client, "jurisdiction@example.com", "Customers open a current account in USD through the branch.")
    run = _run(client, headers, project_id, credential_id, target_trajectory_count=12, event_budget=None, language="en", jurisdiction="tr",
               sub_domains=["onboarding_and_kyc", "deposits"]).json()
    assert run["config"]["jurisdiction"] == "tr" and run["generation"]["jurisdiction"] == "tr"
    assert _currencies(run) == {"TRY"}
    names = {obj["attributes"].get("product_name") for obj in run["bundle"]["objects"] if obj["object_type"] in {"account", "product_offering"}}
    assert "vadesiz hesap" in names
    system = run["bundle"]["samples"][0]["sequences"][0]["contexts"][0]["segments"][0]["text"]
    assert "Jurisdiction Turkey. KYC: Kimlik, MASAK" in system
    overridden = [item for item in run["generation"]["steering"]["facts"]["unsupported"] if "USD" in item]
    assert overridden == ["The documents name USD; the Turkey profile sets TRY, which is used."]

    runtime.judge = RecordingJudge()
    client.post(f"/runs/{run['id']}/evaluate", headers=headers, json={})
    brief = next(call["prompt"] for call in runtime.judge.calls if call["rubric"] == "helpfulness")
    assert "Jurisdiction: Turkey, amounts in TRY. KYC rules: Kimlik" in brief and "çipli T.C. kimlik kartı" in brief
    manifest = client.get(f"/runs/{run['id']}/export/manifest.json", headers=headers).json()
    assert manifest["data_card"]["jurisdiction_profile"] == "Turkey (TRY)"

    uk = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None, jurisdiction="uk").json()
    assert _currencies(uk) == {"GBP"}
    neutral = _run(client, headers, project_id, credential_id, target_trajectory_count=6, event_budget=None, language="tr").json()
    assert neutral["config"]["jurisdiction"] == "neutral" and _currencies(neutral) == {"USD"}


def test_jurisdictions_are_listed_and_unknown_ones_refused(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    banking = next(item for item in client.get("/sectors").json()["data"] if item["id"] == "banking")
    assert [(item["id"], item["currency"], item["language"]) for item in banking["jurisdictions"]] == [("neutral", None, None), ("uk", "GBP", "en"), ("tr", "TRY", "tr")]
    headers, project_id, credential_id = _study_with(client, "jurisdiction-bad@example.com", "Plain notes.")
    assert _run(client, headers, project_id, credential_id, jurisdiction="fr").status_code == 422
    assert client.get(f"/projects/{project_id}/facts", headers=headers, params={"jurisdiction": "fr"}).status_code == 422
    assert "USD" not in json.dumps(client.get(f"/projects/{project_id}/facts", headers=headers).json()["explicit"])
