from dataclasses import replace
from types import SimpleNamespace

import pytest

from sectors.gates import GATES, complete_spec, run_gates
from sectors.registry import get_sector, known_sectors


@pytest.mark.parametrize("sector_id", known_sectors())
def test_every_registered_pack_passes_the_gates_banking_passes(sector_id):
    results = run_gates(get_sector(sector_id))
    assert [gate.name for gate in results] == [gate.__name__ for gate in GATES]
    failed = [f"{gate.name}: {gate.detail}" for gate in results if not gate.passed]
    assert not failed, failed


def test_a_pack_missing_a_phrase_or_an_operation_fails_the_spec_gate():
    telecom = get_sector("telecom")
    phrases = {**telecom.pack.phrases, "tr": {key: value for key, value in telecom.pack.phrases["tr"].items() if key != "port.failed"}}
    operations = {key: value for key, value in telecom.pack.operations.items() if key != "bill.paid"}
    broken = SimpleNamespace(
        id="broken", sub_domains=telecom.sub_domains, default_sub_domains=telecom.default_sub_domains, languages=telecom.languages,
        lifecycle=telecom.lifecycle, pack=replace(telecom.pack, phrases=phrases, operations=operations),
    )
    gate = complete_spec(broken)
    assert not gate.passed and "port.failed has no tr phrase" in gate.detail and "bill.paid has no named operation" in gate.detail
