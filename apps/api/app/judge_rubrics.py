"""Rubrics the studio registers with the engine, so the judge can score what the code scorers already score.

`process_conformance` and `decision_score` are code signals (packages/sectors/src/sectors/scorers.py). The judge scores
the same two questions from the rendered journey and the brief, and each cycle reports how often the judge and the code
agree. The code verdict stays the signal; these rubrics measure the judge, and they never decide acceptance.

Each is a declarative engine rubric: the engine normalises the 1-5 score to 0-1, so a judge score and a code score sit
on the same scale.
"""

from __future__ import annotations

# A short reason keeps a judge that works through the arithmetic inside the engine's answer budget.
_OUTPUT = 'Output ONLY a single JSON object: {"score": integer 1-5, "reason": string}. Keep the reason under 40 words.'
_TEMPLATE = "BRIEF AND QUESTION:\n{prompt}\n\nJOURNEY:\n{response}\n\nReturn your JSON verdict now."

PROCESS_CONFORMANCE = {
    "name": "process_conformance",
    "description": "How typical each step of a journey is under the sector's reference process.",
    "system_prompt": (
        "You are an evaluation judge for synthetic customer journeys. The brief describes the sector's reference "
        "process, and its reference next steps give, wherever the journey could go more than one way, the share of "
        "journeys taking each option. At each of those steps, divide the share of the option the journey took by the "
        "share of the most common option there, so the most common option counts 1 and a rarer one less. Average "
        "those ratios over the listed steps only: a step with one legal option is not listed and does not count, so "
        "a journey with a single listed step scores that step's ratio. Then score 5 for an average near 1, 4 near "
        "0.75, 3 near 0.5, 2 near 0.25, and 1 near 0. A step the process does not allow at that point counts 0. " + _OUTPUT
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

# Rubrics answered by arithmetic on numbers the question gives: asked once at temperature 0, since sampling them only
# adds noise, and a second sample at 0.7 once turned a 0 into a 1.
DETERMINISTIC = frozenset({"process_conformance"})

QUESTIONS = {
    "process_conformance": "Score how typical each step of this {label} journey is under the reference process.",
    "decision_score": (
        "Score whether each outcome decision in this {label} journey took a choice as good as the best one there. A "
        "choice clearly worse than another available one, such as giving up where going on was open, lowers the score."
    ),
}

# Provider-written turns pass code checks for their events, amounts, identifiers, and language, and can still change what
# happened: a declined guarantee written as a cancellation. The judge reads a written turn against the facts it had to
# state; a turn it calls unfaithful in every repeat goes back to its template (decision 14).
TURN_FAITHFULNESS = {
    "name": "turn_faithfulness",
    "description": "Whether a written assistant turn states the events it had to report, with their meaning, and nothing more.",
    "system_prompt": (
        "You check assistant turns that a model wrote for synthetic customer-service conversations. The facts give, in "
        "order, the events the turn had to report and the template it replaced, which states each event plainly with "
        "any amount and wait. The turn is faithful when it states every one of those events with its meaning unchanged, "
        "keeps each amount and wait, and adds nothing the facts do not give: no other outcome, cause, decision, or event. "
        "Wording, tone, and speaking to the customer directly are free. A declined guarantee written as a cancelled "
        "reservation, an approval written where the case was only referred, or a reason the facts never give is "
        'unfaithful. Output ONLY a single JSON object: {"faithful": boolean, "reason": string}.'
    ),
    "user_prompt_template": "FACTS THE TURN HAD TO STATE:\n{expected}\n\nQUESTION:\n{prompt}\n\nWRITTEN TURN:\n{response}\n\nReturn your JSON verdict now.",
    # A judge that finds a turn faithful may give no reason, so only the verdict is required.
    "expected_keys": ["faithful"],
    "score": {"kind": "boolean", "key": "faithful"},
    "requires_expected": True,
}

FAITHFULNESS_QUESTION = "Is this {language} turn, written for a {label} conversation, faithful to the facts?"

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
