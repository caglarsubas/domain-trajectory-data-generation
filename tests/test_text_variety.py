"""Text worth training on (Slice 12): phrasings drawn from the text stream, waits stated, and customer lines to spare."""

import re
from dataclasses import replace

import pytest

from sectors.banking.corpus import CorpusSteering, steering_from_text
from sectors.banking.spec import PACK
from sectors.gates import CUSTOMER_LINES, PHRASINGS, text_variety
from sectors.journeys import generate_bundle
from sectors.registry import get_sector, known_sectors

SETTINGS = dict(
    sub_domains=list(PACK.lifecycle.sub_domains), language="en", target_trajectory_count=8, event_budget=None, min_events=6,
    max_events=20, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
    consumer="post_training", target_family="llm", seed="variety", group_size=4,
)
DETAIL = re.compile(r" \(([^()]*)\)\.$")
ENGLISH = {"the", "and", "was", "were", "has", "have", "been", "with"}


def _bundle(pack=PACK, **overrides):
    return generate_bundle(pack, steering_from_text, CorpusSteering(None, None, (), ()), **{**SETTINGS, **overrides})


def _narrated(bundle):
    """Per sequence: its events in order and the sentences its assistant turns hold."""
    events = {event.event_id: event for event in bundle.events}
    trajectories = {item.trajectory_id: item for item in bundle.trajectories}
    for sample in bundle.samples:
        for sequence in sample.sequences:
            text = " ".join(segment.text for context in sequence.contexts for segment in context.segments if segment.role == "assistant")
            yield [events[item] for item in trajectories[sequence.trajectory_id].event_ids], re.split(r"(?<=\.)\s+", text)


def test_phrasings_change_the_words_and_never_the_journeys():
    plain = _bundle(replace(PACK, variants={}))
    varied = _bundle()
    for field in ("events", "trajectories", "event_objects", "state_transitions"):
        assert getattr(plain, field) == getattr(varied, field)
    texts = lambda bundle: [sentences for _, sentences in _narrated(bundle)]
    assert texts(plain) != texts(varied)


@pytest.mark.parametrize("language", ["en", "tr"])
def test_every_sentence_is_a_phrasing_of_its_event_with_its_own_details(language):
    phrases, variants = PACK.phrases[language], PACK.variants[language]
    later, since = {"en": ("later", "after the previous one"), "tr": ("sonra", "bir öncekinden")}[language]
    used = set()
    for journey, sentences in _narrated(_bundle(language=language)):
        assert len(sentences) == len(journey)
        seen = set()
        for index, (event, sentence) in enumerate(zip(journey, sentences)):
            found = DETAIL.search(sentence)
            body = sentence[: found.start()] + "." if found else sentence
            assert body in (phrases[event.event_type], *variants[event.event_type]), sentence
            used.add(body)
            detail = found.group(1) if found else ""
            wait = (event.event_time - journey[index - 1].event_time).total_seconds() / 3600 if index else 0
            if event.event_type in seen:
                assert since in detail, sentence
            elif wait >= 1:
                assert detail.endswith(later) and since not in detail, sentence
            else:
                assert later not in detail, sentence
            seen.add(event.event_type)
    assert used - set(phrases.values()), "no variant phrasing was drawn"


def test_the_writer_gets_the_sentences_the_sequence_holds():
    groups = []
    _bundle(writer=lambda group: groups.append(group))
    assert groups
    for group in groups:
        for skeleton in group.sequences:
            for turn in skeleton.turns:
                assert " ".join(event["template"] for event in turn.events) == turn.template


@pytest.mark.parametrize("sector_id", known_sectors())
def test_every_pack_has_three_plain_phrasings_of_every_event_and_customer_lines_to_spare(sector_id):
    pack = get_sector(sector_id).pack
    for lang in pack.languages:
        assert set(pack.variants[lang]) == set(pack.phrases[lang])
        for name, canonical in pack.phrases[lang].items():
            phrasings = (canonical, *pack.variants[lang][name])
            assert len(set(phrasings)) == len(phrasings) >= PHRASINGS, (lang, name)
            for text in phrasings:
                assert text.endswith(".") and not re.search(r"[\d()_:;]", text) and name not in text, (lang, text)
                words = set(re.findall(r"[a-zçğıöşü]+", text.lower()))
                assert not (words & ENGLISH if lang == "tr" else set(text) & set("çğıöşüÇĞİÖŞÜ")), (lang, text)
        openings = [line for lines in pack.openings[lang].values() for line in lines]
        assert len(set(openings)) >= CUSTOMER_LINES and len(set(pack.follow_ups[lang])) >= CUSTOMER_LINES
        assert set(pack.openings[lang]) == set(pack.openings["en"])


@pytest.mark.parametrize("sector_id", known_sectors())
def test_every_pack_holds_half_its_sentences_distinct_from_templates_alone(sector_id):
    gate = text_variety(get_sector(sector_id))
    assert gate.passed, gate.detail
