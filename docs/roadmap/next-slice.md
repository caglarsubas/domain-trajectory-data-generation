# Next slice: a roadmap review

Status: approved on 30 September 2026, with decision 27 in the [overview](overview.md#decisions), as recommended; it has shipped, so a review comes next. Slices 18 and 19, approved on 29 September, have shipped; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `43c5a67`.

## Why this comes next

The review re-ran every gate against `43c5a67`, and every pack passes all nine, with the same measures as at `e00c382`:

| Pack | Distinct sequences in 64 journeys | Distinct sentences, English and Turkish | Lowest sub-domain's accepted groups |
|---|---|---|---|
| Airline | 57 | 62% | check-in and boarding, 32% |
| Banking | 50 | 60% | cards and payments, 30% |
| Hotel | 55 | 53% | in-stay services, 27% |
| Insurance | 43 | 53% | servicing, 32% |
| Telecommunications | 51 | 56% | activation and porting, 27% |

A 10,000-sequence banking run costs what it did at `e00c382`. Timed on the same machine, the two commits interleaved, it took 4.7 to 4.9 seconds plain against 4.9 to 5.2, and 7.3 to 8.0 with episodes, decision records, and the decision-score signal against 6.9 to 7.4. CI passes on `main`, and no pull request is open.

The data meets every clause it is measured against, calibrated waits included. What remains is whether a judge cycle finishes.

- **The whole cycle works, live.** With the machine kept awake, a full cycle judged a hotel run calibrated from the hotel booking demand data, over booking, changes and cancellations, and arrival (198 verdicts, 12 minutes). It covered three sampled journeys and their controls, three repeats of each rubric, and the fair realism comparison, with `qwen3.6:27b` and `gemma4:26b`. The run was accepted, and for the first time live both helpfulness and correctness decided:

  | | `qwen3.6:27b`, primary | `gemma4:26b` |
  |---|---|---|
  | Helpfulness controls scored lower | 2 of 4 (a missing step 1 of 3, a slow wait 1 of 1) | 0 of 6: blind |
  | Correctness controls scored lower | 4 of 6 (a missing step 2 of 3, reversed 2 of 3) | 4 of 6 (missing step 1 of 3, reversed 3 of 3) |
  | Pairwise, a journey against its flawed copy | 2 of 2 | 2 of 2 |
  | Picked the real case over an out-of-order journey | 100% of 8 | 88% of 8 |
  | Picked the real case over the run's journeys | 56% of 32 | 47% of 32 |

  It is the first cycle in which a judge caught a missing step by scoring a journey alone. Both judges see realism and cannot tell the run's journeys from real ones, and no step outside the scope or time finer than a day was shown.
- **Slice 19's failed cycles were the laptop sleeping, not the engine.** The engine's investigation, `llm_inference_engine` #121, matched each of the four 504s to the host asleep with its lid closed. The engine's clock ran on through sleep, so its 240-second limit fired the moment the host woke. Slice 19's notes blamed the engine; this corrects them. A cycle still stops at its first failed call and keeps none of the verdicts before it, though asked again the call answers in seconds. Each of those cycles lost up to 16 minutes and everything it had heard.
- **Some verdicts run out of room.** 3 of this cycle's 198 verdicts were unreadable, all `qwen3.6:27b` helpfulness answers. Each justification kept reasoning, re-reading the question, until the engine's 512-token answer limit cut it off. The engine counts 49 of 1,347 `qwen3.6:27b` verdicts cut off the same way, and leaves raising its limit to a follow-up. The studio already asks for a reason under 40 words in the pairwise and realism questions and the rubrics it registers, but not in helpfulness, correctness, or safety.

## Delivered: Slice 20, a cycle that finishes

Delivered in one pull request covering tasks 1, 2, and 4. Task 3 was measured, found to change what the judges score, and dropped at the owner's choice, so the exit's last clause is left unmet.

1. **A failed call asked again** (decision 27). A judge call that fails with a timeout or a busy engine is asked once more before the cycle fails, and the job's progress says so while it waits.
2. **A cycle resumed, not restarted.** A cycle keeps its verdicts as they come. When it fails anyway, asking the judge again resumes it: it asks only what it has not yet heard, and the run page says how far the failed cycle got.
3. **Verdicts that fit.** Helpfulness, correctness, and safety ask for a reason under 40 words, as the other questions do.
4. **An engine kept awake.** The README says an engine on a laptop answers 504 once the laptop sleeps, and to keep the host awake while cycles run.

Exit: with a test engine that times out one call, a cycle completes with every verdict; a cycle stopped halfway resumes and asks none of the verdicts it holds again; and in a live cycle fewer than 1 in 100 verdicts is unreadable.

- **A failed call asked again.** A call the engine answers 504 or 503 is asked once more after five seconds, and the job's progress says so. A test engine that times out one call and is busy for another yields every verdict, exactly as a clean run does; a call that fails twice, or a rejected key, still fails the cycle.
- **A cycle resumed, not restarted.** Each answer is kept beside the run as it arrives, keyed by the judge and the exact question. When a cycle stops, the run page says how far it got and offers to resume, and asking the judge again asks only what it has not heard: through the API, a cycle stopped after 6 answers finished by asking only the rest. A question worded differently is asked again, and the kept answers go once the cycle is stored.
- **An engine kept awake.** The README says an engine on a laptop answers 504 once the laptop sleeps, a closed lid included.
- **Verdicts that fit, dropped.** Asked for a justification under 40 words, `qwen3.6:27b` fit the engine's answer: no verdict of a live cycle's 198 was unreadable, against 3 before, and its longest helpfulness answer fell from 2,504 characters to 355. But it scored the same journeys differently. Replaying two cycles' questions both ways, correctness with the cap called two of three sound journeys incorrect that it called correct without it, and caught 3 of 6 flawed copies against 4 of 6. Helpfulness with the cap scored sound journeys 1 to 3.7 instead of 5, and 10 of 12 flawed copies 1. The judge reasons in its justification, so the questions keep it uncapped, and a justification the engine cuts off stays an unreadable verdict, 1.5% to 3.6% of `qwen3.6:27b`'s, until the engine raises its limit.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
