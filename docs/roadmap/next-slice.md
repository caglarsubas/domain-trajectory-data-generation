# Next slice: a roadmap review

Status: approved on 28 September 2026, together with the queued Slice 15 and decisions 18 to 21 in the [overview](overview.md#decisions), all as recommended. Slices 11 to 13, approved on 28 September, have all shipped; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `5de8b85`.

## Why this comes next

The review re-ran every gate against `5de8b85`, and every pack passes all nine:

| Pack | Distinct sequences in 64 journeys | Distinct sentences, English and Turkish | Lowest sub-domain's accepted groups |
|---|---|---|---|
| Airline | 57 | 62% | check-in and boarding, 32% |
| Banking | 47 | 59% | cards and payments, 30% |
| Hotel | 55 | 53% | in-stay services, 27% |
| Insurance | 43 | 53% | servicing, 32% |
| Telecommunications | 51 | 56% | activation and porting, 27% |

A 10,000-sequence banking run costs what it did at `eaba152`. Timed on the same machine, the two commits interleaved, median 12 against 10 seconds plain and 16 against 14 with episodes, decision records, and the decision-score signal, within each commit's own spread of 6 to 27 seconds. The full run still costs about 1.4 times the plain one.

The findings are about whether the judge's acceptance means anything, and about calibration where the data is thin.

- **The judges cannot tell a flawed journey from a sound one by scoring it, and the cycle still accepts.** A live cycle on the local `llm_inference_engine` asked `gemma4:26b` and `qwen3.6:27b` about three banking journeys, three controls with one known defect each, and a pairwise control, three repeats each at temperature 0.7 (158 verdicts, 9 unreadable, 85 minutes):

  | Rubric | `gemma4:26b` | `qwen3.6:27b` |
  |---|---|---|
  | Helpfulness, which decides acceptance | 5 of 5 on every journey; blind to the missing step and the slow wait | 3.8 on average; caught the slow wait, not the missing step |
  | Correctness, which decides acceptance | blind to the missing step; passed the reversed control journey | blind to the missing step; passed the reversed control journey |
  | Process conformance | scored the copy with a missing step 1.0, its original 0.25 | 1.0 against 0.75 |
  | Decision score | blind to the worse choice | caught it, 1.0 to 0.5 |
  | Pairwise: a journey against its flawed copy, both orders | picked the original in all 4 readable verdicts | picked the original in all 6 |

  Every rubric that asks for a score on its own was blind to some defect, for some judge, and Slice 11's live cycle had found the same for helpfulness. The one question with a right answer, which of two journeys is sounder, both judges answered right every time. Yet the cycle accepted the run on helpfulness, correctness, and safety.
- **The studio's default judge pair can be one model.** The engine serves `qwen3.8:27b` and `gemma4:26b` from one substitution group (#58), so the defaults, `qwen3.8:27b` first and `gemma4:26b` second, can be one model judging twice. Each verdict records the model that answered, and nothing flags it.
- **Calibration follows the data where the data is rich.** Measured at `afd08ea`, which #63 leaves unchanged for these scopes, from the real files, 1,500 journeys over every sub-domain, two seeds: delayed flights arrive late in 84% and 80% of calibrated airline journeys against 84% in the data, and hotel reservations are cancelled after confirmation in 33% against 36%. Banking from BPI 2017, after #60 and #62, ends submitted applications approved in 43% and 45%, declined in 23% and 20%, and abandoned in 34% and 35%, against 49%, 13%, and 37%; fitness is 0.48 and next-step divergence 0.39.
- **Banking declines too often, for a reason the data can show.** Since #62 maps A_Validating to the start of KYC, the data holds no passed check, so the decision after one reads the shares after submission and the start of KYC. There, a direct decline is common, while most approvals come later, after a review the generated journey never took: 1,639 declines against 2,294 approvals, a 42% decline share at the decision.
- **Telecommunications and insurance have no public source to calibrate from.** The terms review found the FCC's complaints record no step after filing, and the Texas Department of Insurance's data waits on its written confirmation. Those packs calibrate only from a user's own logs, which the studio reads and maps but gives no template or preview for.
- **Text is done.** Every pack holds 53% to 62% of its sentences distinct from templates alone, a whole group is written per call, and live both judges caught planted unfaithful turns in English and Turkish.

Outside the code, the studio's `.env` still points the judge at an ngrok tunnel that answers 404; the engine runs on the host at port 8080, which the Compose stack reaches as `http://host.docker.internal:8080`.

## Delivered: Slice 14, acceptance the judges can earn

Delivered in one pull request covering tasks 1 to 4, and the exit criteria are met.

1. **Controls for every sampled journey** (decision 19). Each sampled journey gets the flawed copies the pack confirms for it, a missing step, a worse choice, a slow wait, and its events reversed, instead of at most one of each across the sample. Discrimination then rests on up to six controls per rubric and judge, not the one or two that make today's rates all or nothing. Controls are asked once at temperature 0, as conformance is since #58, so a cycle generates about what it does today while asking more.
2. **A rubric decides only where its judge sees** (decision 18). Helpfulness and correctness decide acceptance for a judge only if, in the same cycle, that judge scores at least half of their controls lower than the originals, and for correctness also catches the reversed journey. Otherwise they keep scoring and writing revision notes, and the cycle says they did not decide. When no rubric decides, the run is accepted or not on the code's checks alone, and the run page says the judges could not tell.
3. **Helpfulness reads what it is asked about.** Its question names skipped steps and waits far past a step's usual time, but the judge is never told a step's usual time. The prompt gives each step's wait range, as conformance's gives the next-step shares, and the next live cycle measures whether helpfulness then sees the slow wait and the missing step.
4. **Two models in the judge pair** (decision 20). The default primary judge becomes `qwen3.6:27b`, which saw the most defects here, with `gemma4:26b` second. A cycle whose two judges were served by one model says so on the run page, and reports agreement as one judge's.

Exit: in a live cycle, acceptance comes only from rubrics whose judge scored their controls lower, each measured over at least three controls; a rubric blind to its controls decides nothing that cycle; and a cycle judged twice by one model says so.

Live on the local engine, three banking journeys, their 11 controls, and a pairwise control, three repeats for the journeys' own rubrics and one at temperature 0 for the controls (152 verdicts, 5 unreadable, 33 minutes). The engine served the second judge, named `gemma4:26b`, with `qwen3.8:27b` from its substitution group, which `served_by` records:

| Rubric | `qwen3.6:27b`, primary | second, served by `qwen3.8:27b` |
|---|---|---|
| Helpfulness | 1 of 6 controls lower (slow wait 1 of 3, missing step 0 of 3): does not decide | 3 of 6 (slow wait 3 of 3, missing step 0 of 3) |
| Correctness | 2 of 5 lower (reversed 2 of 2, missing step 0 of 3): does not decide | 2 of 6 (reversed 2 of 3) |
| Process conformance, before #68 left it to code | 0 of 3 | 0 of 3 |
| Decision score | 1 of 2 | 0 of 2 |
| Pairwise control | picked the original 2 of 2 | 2 of 2 |

Neither helpfulness nor correctness decided, so the run was accepted on the code's checks and safety, and the run page says the judges could not tell. With each step's usual wait in its question, the second judge caught every stretched wait on helpfulness; no judge caught a missing step on any rubric that scores a journey on its own. A second cycle naming `qwen3.8:27b` and `gemma4:26b` was served by `qwen3.8:27b` for both and flagged `same_model`.

## Delivered: Slice 15, calibration people can bring

Delivered in two pull requests, and the exit criteria are met. Every approved slice has now shipped; the next step is a review of the roadmap against `main`.

1. **Banking's passed check.** Map a BPI activity to `kyc.passed`, or read the decision after a passed check from the shares the data backs there, so banking declines land within 5 points of the data's 13% while approvals and abandonment stay within 5 of theirs.
2. **A template per pack** (decision 21). The studio offers, for each pack, a CSV template of its events with a short example log, so a telecommunications or insurance team can export its own journeys in a shape the mapper reads without guessing.
3. **A preview before a run.** After a log is mapped, the data source shows what calibration would change: for the steps it moves most, the pack's share against the data's, and the events the data cannot see.

Exit: banking calibrated from BPI 2017 declines within 5 points of 13%; a telecommunications log written from the template calibrates a run to a next-step divergence under 0.1; the preview matches the calibrated run's shares.

- **Banking's passed check** (#66, from another session). The data holds no passed check, so a repeat of validation is followed through the data, and a step the pack reaches only through an unseen event counts for it. Calibrated from BPI 2017 over every sub-domain, two seeds, submitted applications end approved in 50% and 52%, declined in 11% and 11%, and abandoned in 38% and 36%, against 49%, 13%, and 37%.
- **A template per pack.** `GET /sectors/{id}/log-template/events.csv` lists a pack's events with their sub-domains and meanings, and `example.csv` is 20 of the pack's own journeys as `case_id`, `activity`, and `timestamp`. An activity named exactly as a pack event maps to itself before any word is matched. The data sources panel links both.
- **A preview before a run.** Each calibration carries what it would change in a run over every sub-domain, from 600 journeys with and without it: the steps it moves most, after events left at least 50 times, with the pack's, the calibrated run's, and the data's shares; the events the data cannot see; and the weighted divergence with and without it. The panel shows it under the data source.

A telecommunications team's log written from the template, from a process that abandons orders, fails credit checks and ports, and declines plan changes three to four times as often as the pack's priors (600 journeys), maps whole and calibrates:

| Step | Pack | Preview, calibrated | Data | A calibrated run of 1,500 |
|---|---|---|---|---|
| order started, then abandoned | 12% | 34% | 33% | 31% |
| port requested, then failed | 13% | 33% | 42% | 35% |
| complaint received, then escalated | 5% | 23% | 23% | 17% |

The calibrated run's next-step divergence is 0.082, under 0.1, and every share the preview shows is within 0.06 of the run's. That criterion, though, does not tell a calibrated run from an uncalibrated one: the plain divergence is a mean over events however rarely the run leaves them, and resampling the team's own process scores 0.131 at 400 journeys and 0.089 at 1,500, while the uncalibrated pack scores 0.095. Counting each event by how often the run leaves it, the calibrated run reaches the team process's own 0.014 against 0.024 uncalibrated, so the quality report now gives that weighted divergence too, and the preview uses it.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
