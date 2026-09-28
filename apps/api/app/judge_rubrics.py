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
        "process, and its reference next steps give, wherever the journey could go more than one way, the share of "
        "journeys taking each option. At each of those steps, divide the share of the option the journey took by the "
        "share of the most common option there, so the most common option counts 1 and a rarer one less. Average "
        "those ratios over the steps, then score 5 for an average near 1, 4 near 0.75, 3 near 0.5, 2 near 0.25, and "
        "1 near 0. A step the process does not allow at that point counts 0. " + _OUTPUT
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

# The judge writes a study's rubrics through the eval route, which asks a reasoning judge to answer without thinking and
# in JSON, inside a scheduler slot. The engine calls whatever it reads a "response"; here that is a group of journeys, and
# the verdict is a rubric for them. `fit` is the judge's own view of how clearly the group separates on its criteria.
RUBRIC_PROPOSAL = {
    "name": "rubric_proposal",
    "description": "Write one study-specific rubric from a group of journeys and the study's reference.",
    "system_prompt": (
        "You write evaluation rubrics for studies of synthetic customer journeys. Read what the prompt asks for, the "
        "study brief with its reference passages, and a group of journeys from the study, and write the one rubric "
        "the prompt asks for. Criteria must be facts a reader can check in a single journey's events, order, and "
        "timing, specific to this study's sector and scope, not generic advice. Keep it short: a title under 8 "
        "words, a one-sentence description, 3 to 5 criteria of under 25 words each, and anchors of under 25 words. "
        'Output ONLY a single JSON object: {"title": string, "description": string, "criteria": [string], '
        '"anchors": {"5": string, "3": string, "1": string}, "fit": integer 1-5}, where the anchors say what a '
        "journey scoring 5, 3, and 1 shows, and fit says how clearly the journeys in the group differ on these criteria."
    ),
    "user_prompt_template": "WHAT TO WRITE:\n{prompt}\n\nTHE GROUP OF JOURNEYS:\n{response}\n\nReturn your JSON rubric now.",
    "expected_keys": ["title", "description", "criteria", "anchors", "fit"],
    "score": {"kind": "number", "key": "fit", "min": 1, "max": 5},
}

# What each kind of study rubric covers, after the code's solution and behavior rubrics (sectors/scorers.py), which a
# study rubric of that kind is compared with.
STUDY_KINDS = {
    "solution": {
        "signal": "solution_rubric",
        "ask": (
            "Write this study's solution rubric: what the resulting state of a journey should be. Say which endings "
            "count as resolved for this study, which decisions must not be left open at the end, and which of the "
            "study's sub-domains a journey should reach."
        ),
    },
    "behavior": {
        "signal": "behavior_rubric",
        "ask": (
            "Write this study's behavior rubric: how the path should be built. Say which steps should not be undone "
            "or repeated, which waits should be prompt and how prompt, and which order of steps the reference expects."
        ),
    },
}
