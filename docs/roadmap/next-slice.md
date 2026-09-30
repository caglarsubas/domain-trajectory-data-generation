# Next slice: questions the studio owns

Status: approved on 30 September 2026, with decision 28 in the [overview](overview.md#decisions) and closing the roadmap after it, all as recommended. Slice 20, approved earlier the same day, has shipped without its third task; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `96fd248`.

## Why this comes next

The review re-ran every gate against `96fd248`, and every pack passes all nine, with the same measures as at `43c5a67`:

| Pack | Distinct sequences in 64 journeys | Distinct sentences, English and Turkish | Lowest sub-domain's accepted groups |
|---|---|---|---|
| Airline | 57 | 62% | check-in and boarding, 32% |
| Banking | 50 | 60% | cards and payments, 30% |
| Hotel | 55 | 53% | in-stay services, 27% |
| Insurance | 43 | 53% | servicing, 32% |
| Telecommunications | 51 | 56% | activation and porting, 27% |

A 10,000-sequence banking run costs what it did at `43c5a67`. Timed on the same machine, the two commits interleaved, it took 4.6 seconds plain against 4.5 to 4.9, and 6.8 to 7.0 with episodes, decision records, and the decision-score signal against 6.9 to 7.1. CI passes on `main`, and no pull request is open.

This review was to say whether the roadmap has reached its end. Every clause of the purpose is met, but two, and those two are held back from outside the studio. Telecommunications and insurance have no public data, so a team brings its own log. And today's judges see a missing step only now and then. One dependency the review found is inside it, though.

- **The engine fixed what it owed.** `llm_inference_engine` #121 traced Slice 19's failed cycles to the laptop sleeping. #122 keeps the score of a verdict cut off at the engine's 512-token answer limit, marked `truncated`, which the studio already reads as a score.
- **The engine also rewrote the questions the studio scores by.** #122 made the engine's built-in helpfulness, correctness, and pairwise rubrics ask for a justification of one or two sentences, without a live measure. The studio asks those rubrics by name, so their wording is the engine's. The review asked review 6's cycle again against today's engine: the same run, sampled journeys, controls, and questions (198 verdicts, 8 minutes, with the machine kept awake).

  | | Review 6's engine | Today's engine, with #122 |
  |---|---|---|
  | Unreadable verdicts | 3 | 0 |
  | Verdicts with a different score | | 16 of 198 |
  | `qwen3.6:27b`: sound journeys called correct | 8 of 9 (0.89) | 5 of 9 (0.56) |
  | `qwen3.6:27b`: helpfulness of the sound journeys | 5.0 | 4.6 |
  | `qwen3.6:27b`: correctness controls scored lower | 4 of 6 | 4 of 6 |
  | `gemma4:26b`: missing steps its correctness caught | 1 of 3 | 0 of 3 |
  | `gemma4:26b`: real case over an out-of-order journey | 88% | 75% |
  | Justification length, median | 598 characters | 255 |

  The run is still accepted, but correctness now clears its bar by a hair: the primary judge calls four of nine verdicts on sound journeys incorrect, where it called one. Slice 20 found the same effect when the studio capped the justification itself, and kept its questions uncapped. The engine's built-in wording is outside the studio's hands, so the studio's acceptance moved when the engine changed.

## Next: Slice 21, questions the studio owns

1. **The studio's own rubrics** (decision 28). Helpfulness, correctness, safety, and pairwise quality are registered as the platform tenant's rubrics, worded as the engine's built-ins were through review 6, with the studio's questions unchanged, and asked by name, as the code rubrics and `decision_score` already are. The cycle records each one's digest.
2. **A wording change said.** When a rubric's digest differs from the one the study's last cycle recorded, the run page says so beside the scores, so a change to what the judges are asked never passes unseen.
3. **Cut-off verdicts still scored.** The engine keeps the score of a tenant rubric's verdict cut off at its limit, as #122 does for its built-ins, and the studio reads it as a score.

Exit: asked review 6's cycle again against today's engine, the studio's own questions give `qwen3.6:27b` 8 of 9 sound-journey verdicts correct or more, as review 6 did, with no verdict unreadable. A test changes a rubric's wording, and the run page says so.

After Slice 21, the roadmap closes, as the owner approved: every clause is met as far as the data and today's judges allow. CI and the gates stay as they are. A new roadmap opens when a study asks for something out of scope, or when a judge model sees what these cannot.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
