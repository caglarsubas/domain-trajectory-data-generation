"""Rubrics the studio registers with the engine, so the judge can score what the code scorers already score.

`process_conformance` and `decision_score` are code signals (packages/sectors/src/sectors/scorers.py). The judge scores
the same two questions from the rendered journey and the brief, and each cycle reports how often the judge and the code
agree. The code verdict stays the signal; these rubrics measure the judge, and they never decide acceptance.

Each is a declarative engine rubric: the engine normalises the 1-5 score to 0-1, so a judge score and a code score sit
on the same scale.
"""

from __future__ import annotations

_OUTPUT = 'Output ONLY a single JSON object: {"score": integer 1-5, "reason": string}.'
_TEMPLATE = "BRIEF AND QUESTION:\n{prompt}\n\nJOURNEY:\n{response}\n\nReturn your JSON verdict now."

PROCESS_CONFORMANCE = {
    "name": "process_conformance",
    "description": "How typical each step of a journey is under the sector's reference process.",
    "system_prompt": (
        "You are an evaluation judge for synthetic customer journeys. The brief describes the sector's reference "
        "process. For each event in the journey, decide whether it is a step the reference process would usually "
        "take next, given the events before it. Rare but allowed steps lower the score; steps the process does not "
        "allow at that point lower it most. Score 5 when every step is the usual next step, 3 when most are usual "
        "and some are unusual, 1 when most steps are unusual or out of order. " + _OUTPUT
    ),
    "user_prompt_template": _TEMPLATE,
    "expected_keys": ["score", "reason"],
    "score": {"kind": "number", "key": "score", "min": 1, "max": 5},
}

DECISION_SCORE = {
    "name": "decision_score",
    "description": "Whether each outcome decision in a journey took a choice as good as the best one there.",
    "system_prompt": (
        "You are an evaluation judge for synthetic customer journeys. An outcome decision is a point where the "
        "process could go more than one way, such as an approval or a decline, or a guarantee or a cancellation. "
        "At each outcome decision in the journey, judge whether the choice taken leads to an outcome as good as the "
        "best choice available there, for the customer and for the business, given the events before it. Score 5 "
        "when every decision took the best available choice, 3 when some took a clearly worse one, 1 when most did. "
        "A journey with no outcome decision scores 5. " + _OUTPUT
    ),
    "user_prompt_template": _TEMPLATE,
    "expected_keys": ["score", "reason"],
    "score": {"kind": "number", "key": "score", "min": 1, "max": 5},
}

CODE_RUBRICS = {rubric["name"]: rubric for rubric in (PROCESS_CONFORMANCE, DECISION_SCORE)}

# The normalised judge score at which the judge's verdict counts as a pass, to set against the code's own pass rule:
# typicality >= 0.5 for conformance, and every decision within 0.9 of the best for the decision score, which asks
# more of the judge than the middle of its scale.
JUDGE_PASS = {"process_conformance": 0.5, "decision_score": 0.75}

QUESTIONS = {
    "process_conformance": "Score how typical each step of this {label} journey is under the reference process.",
    "decision_score": "Score whether each outcome decision in this {label} journey took a choice as good as the best one there.",
}
