# Next slice: a judge that can tell ours from theirs

Status: approved on 29 September 2026, together with the queued Slice 17 and decisions 22 to 24 in the [overview](overview.md#decisions), all as recommended. Slices 14 and 15, approved on 28 September, have shipped; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `74ae828`.

## Why this comes next

The review re-ran every gate against `74ae828`, and every pack passes all nine:

| Pack | Distinct sequences in 64 journeys | Distinct sentences, English and Turkish | Lowest sub-domain's accepted groups |
|---|---|---|---|
| Airline | 57 | 62% | check-in and boarding, 32% |
| Banking | 50 | 60% | cards and payments, 30% |
| Hotel | 55 | 53% | in-stay services, 27% |
| Insurance | 43 | 53% | servicing, 32% |
| Telecommunications | 51 | 56% | activation and porting, 27% |

A 10,000-sequence banking run costs what it did at `5de8b85`: timed on the same machine, the two commits interleaved, 4.5 to 4.8 seconds plain against 4.6 to 5.1, and 6.9 to 7.2 with episodes, decision records, and the decision-score signal against 6.7 to 7.5. The full run costs about 1.5 times the plain one.

The data now meets every clause it is measured against; what remains is what the judge can add, and whether the studio's own stack can reach it.

- **The judges still cannot see a missing step by scoring, and now the run stands on the code, as decided.** A live cycle on the local `llm_inference_engine` asked `qwen3.6:27b` and `gemma4:26b` about three banking journeys, their 11 controls, and a pairwise control (140 verdicts, 1 unreadable after #68's retry, 8 minutes):

  | Rubric | `qwen3.6:27b`, primary | `gemma4:26b` |
  |---|---|---|
  | Helpfulness | 1 of 6 controls lower: does not decide | 1 of 6 |
  | Correctness | 2 of 5 (reversed 2 of 2, missing step 0 of 3): does not decide | 3 of 6 (reversed 3 of 3) |
  | Decision score | 1 of 2 | 0 of 2 |
  | Pairwise: a journey against its flawed copy, both orders | picked the original 2 of 2 | 2 of 2 |

  Neither deciding rubric saw its controls, so the run was accepted on the code's checks and safety, and the run page says the judges could not tell (decision 18). Across this cycle and Slice 14's, no judge caught a missing step on any rubric that scores a journey on its own, while both caught every reversed journey and answered every pairwise question with a right answer correctly. Asked to compare two journeys, the judges are reliable; asked to score one, they are not.
- **The studio's own stack still cannot reach the judge.** `.env` points `INFERENCE_ENGINE_BASE_URL` at an ngrok tunnel that has answered 404 through three reviews, and the studio says so only when a cycle fails. The engine runs on the host at port 8080.
- **Calibration follows the data wherever there is data.** Airline and hotel runs as Slice 13 measured; banking from BPI 2017 after #66 approves 50% to 52%, declines 11%, and abandons 36% to 38% of submitted applications, against 49%, 13%, and 37%; telecommunications and insurance calibrate from a team's own log, mapped whole from the template, with a preview (#69).

## Next: Slice 16, a judge that can tell ours from theirs

1. **Generated against real** (decision 22). Where a study has a data source whose cases are journeys, a cycle sets sampled generated journeys against real cases from that source, blind and in both orders, as event types and times since the case began only, both drawn in the same form. The judge picks the more realistic; a judge that picks the real case about half the time cannot tell them apart.
2. **Reported, never deciding** (decision 23). The cycle reports, per judge, how often it picked the real case, a tie counting half, and the reasons it gave for its picks. A judge that picks the real case in three of four comparisons or more is flagged `distinguishable`, with its reasons on the run page; the comparison decides nothing.
3. **Real cases stay the study's.** They are drawn at calibration from the study's own source, kept only as event types and relative times, sent only to the platform's judge, and never exported.

Exit: in a live cycle, each judge's rate of picking the real case, with its reasons, is reported for a hotel run calibrated from the catalogue and for the same run uncalibrated.

## Queued: Slice 17, a judge the stack can reach

1. **The judge checked before a cycle** (decision 24). The study page shows whether the engine answers, whether its rubric registry does, and whether both judge models are among the ones it lists, and the judge button says why it cannot run instead of queuing a cycle that will fail.
2. **One model, said before.** When the last cycle found both judges served by one model, the study page says so before the next.
3. **A host engine by default.** `.env.example`, Compose, and the README point the stack at an engine on the host, `http://host.docker.internal:8080`, and say how to point it elsewhere.

Exit: with the engine unreachable, the studio says so before a cycle; with the address pointed at the host's engine, a cycle from the Compose stack succeeds.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
