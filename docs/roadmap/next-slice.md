# Next slice: judges that can tell journeys apart

Status: approved on 28 September 2026, together with the queued Slices 12 and 13 and decisions 13 to 17 in the [overview](overview.md#decisions), all as recommended. Slices 8 to 10, approved on 26 September, have all shipped; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `eaba152`.

## Why this comes next

The review re-ran every gate against `eaba152`, and every pack passes all eight:

| Pack | Distinct sequences in 64 journeys | Lowest sub-domain's accepted groups |
|---|---|---|
| Airline | 57 | check-in and boarding, 32% |
| Banking | 49 | cards and payments, 30% |
| Hotel | 55 | in-stay services, 27% |
| Insurance | 43 | servicing, 32% |
| Telecommunications | 51 | activation and porting, 27% |

A 10,000-sequence banking run with episodes, decision records, and the decision-score signal still costs about 1.5 times the plain run. Timed against Slice 8's commit on the same machine, interleaved, the two commits take the same time: 17 to 23 seconds against 9 to 16 plain.

The findings are about what the data and the judge are worth, not whether they run.

- **The judge gives everything the top score.** A live cycle on the local `llm_inference_engine` asked two judges, `gemma4:26b` and `qwen3.6:27b`, three repeats each at temperature 0.7, about three banking journeys (132 verdicts, 2 unreadable, 25 minutes):

  | Rubric | Both judges, every repeat | The code's verdict |
  |---|---|---|
  | Helpfulness | 5 of 5 on every journey | — |
  | Correctness, safety | 1 on every journey | — |
  | Process conformance | 5 of 5 on every journey | 2 of 3 typical; mean typicality 0.71 |
  | Decision score | 5 of 5 on every journey | 2 of 3 took the best choice; mean 0.67 |

  Agreement across models (3 of 3) and across repeats (spread 0) is perfect because there is nothing to disagree about. The judges agree with the code on 2 of 3 journeys, the ones the code passes, and both caught the reversed control journey. The acceptance they grant says little: the only journey they could not score well was the one whose events were put backwards.
- **Pairwise has no right answer.** After #47 it decides nothing. The live cycle preferred the primary journey 0.83 and 0.87 and flipped once per judge. #47 named the follow-up: a pair whose answer is known.
- **Templated text repeats.** In a 64-sequence run over all sub-domains, English:

  | Pack | Distinct phrasings | Distinct sentences | Distinct customer messages |
  |---|---|---|---|
  | Airline | 35 | 23% | 10 |
  | Banking | 28 | 29% | 12 |
  | Hotel | 35 | 16% | 9 |
  | Insurance | 23 | 19% | 9 |
  | Telecommunications | 33 | 18% | 8 |

  Provider-written text varies it, but it is opt-in, costs a call per sequence, and stops at 4,000 calls a run: a 100,000-sequence run keeps templates for 96% of its sequences.
- **Written text is not checked for meaning.** Live on `gemma4:26b`, a Turkish turn for a declined guarantee said the reservation was *cancelled* because no guarantee could be given. It passed every code check: the events, amounts, identifiers, and language were right.
- **Calibration sees one step back.** Calibrated from the real files on 400 journeys, hotel runs match the data after a confirmation (cancelled 37% against 34%), and airline runs match delays at check-in (32% against 33%). But the pack lets only a delayed flight arrive late, and the data's late-arrival share covers every flight: delayed flights arrive late in 32% of calibrated journeys against 84% in the data.
- **Telecommunications and insurance** still calibrate only from logs a user uploads; their catalogue sources wait on a terms review.

Two things outside the code:
- **The studio's judge address is offline.** `.env` points `INFERENCE_ENGINE_BASE_URL` at an ngrok tunnel that answers 404 on every path; the engine itself runs locally on port 8080 with #120. Judge cycles from the Docker stack fail until the address is updated.
- **In flight in another session:** #53 gives the conformance judge the next-step shares the code scores typicality with. Slice 11 builds on it once it merges rather than duplicating it.

## Delivered: Slice 11, judges that can tell journeys apart

Delivered in one pull request covering tasks 1 to 5. A live cycle on the local engine, one repeat, three banking journeys and three controls, 522 seconds:

| Rubric | `gemma4:26b` | `qwen3.6:27b` |
|---|---|---|
| Correctness | caught the missing step, 1 to 0 | caught it, 1 to 0 |
| Decision score | scored the worse choice lower, 1 to 0.5 | lower, 1 to 0.5 |
| Pairwise control | picked the original in both orders | picked the original in both orders |
| Process conformance | lower, 1 to 0.75 | blind, 1 to 1 |
| Helpfulness | blind: 5 to 5 for the missing step, 2 to 2 for the slow wait | blind, 2 to 2 |

With questions that name what to look for, the judges no longer give every journey the top score: helpfulness averaged 4 of 5 and correctness 0.67. Helpfulness, which decides acceptance, is still blind to both defects it was given, and `qwen3.6:27b` is blind on conformance; the run page now says so.

1. **Graded controls.** Each cycle adds, beside the reversed control journey, copies of sampled journeys with one known defect, each confirmed by the pack's own machinery:
   - a required step removed, so the replay fails at one point;
   - an outcome decision swapped for a legal but worse choice, with a lower simulated value;
   - one wait stretched far past its step's range, so the behavior rubric fails.

   The judges score the controls with the same rubrics and repeats as the originals.
2. **Discrimination, per rubric and model.** The cycle reports how often a control scores below its original. A judge that scores its controls as high as the originals on a rubric is flagged `blind_to_defect` (decision 13). The run page shows it next to agreement, so agreement at the ceiling reads for what it is.
3. **Pairwise with a right answer.** A journey against its defective copy, blind, in both orders: the judge should pick the original. The cycle reports pairwise control accuracy per model, the follow-up #47 named.
4. **Questions that say what to look for.** The studio's prompts for helpfulness, correctness, conformance, and decisions name the defects a reader should check: skipped steps, a reversed order, a worse choice, an implausible wait. Their effect is measured by task 2 on the same journeys.
5. **Docs.** README, `docs/evaluation.md`, the contract (controls in `sample`, `agreement.discrimination`, the new flags), the roadmap, and the delivered log.

### Exit criteria

- Every cycle reports, per rubric and model, how often a control with a known defect scores below its original, and the pairwise control's accuracy.
- Tests show a judge that ignores the defects is flagged blind and one that sees them is not.
- A live cycle on the local engine reports discrimination for both judges.

## Queued: Slice 12, text worth training on

- **Template variants.** At least three phrasings of every event in every pack, in English and Turkish, and at least twenty customer openings and follow-ups per pack. They are drawn from the text stream, so journeys do not change.
- **Faithfulness judged.** A `turn_faithfulness` rubric, registered with the engine like the code-comparison rubrics, is asked of a sample of provider-written turns each cycle. It checks that the text states the skeleton's events and adds no outcome. A turn it calls unfaithful in every repeat reverts to its template (decision 14).
- **A group per call.** The writer writes a whole group's sequences in one call, which share the opening and prompt, so the same cap covers up to 16 times more sequences.

Exit: every pack reaches half its sentences distinct in a 64-sequence run with templates alone, in both languages. A cycle catches a planted unfaithful turn. One call writes a whole group.

## Queued: Slice 13, calibration beyond one step, and the last sources

- **Second-order calibration** (decision 15). Next-step shares are taken after the last two events wherever at least 25 observations back them, and after the last event otherwise. The walker, the conformance scorer, and the decision values read them alike.
- **Representativeness** adds the second-order divergence, so a conditional gap shows even when first-order shares match.
- **Telecommunications and insurance sources** (decision 16). A terms review of the FCC's consumer complaints data and the Texas Department of Insurance complaint data, then an adapter for each that its terms allow, calibrating complaint channels and outcomes.

Exit: in a calibrated airline run, delayed flights arrive late within 10 points of the data's 84%, and hotel and banking calibrated shares stay within noise of today's. Each candidate source is added with its licence, or listed with the terms that block it.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
