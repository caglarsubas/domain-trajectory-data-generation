# Next slice: honest data at every scope

Status: approved on 26 September 2026, together with the queued Slices 9 and 10 and decisions 7 to 12 in the [overview](overview.md#decisions), all as recommended. Slices 0 to 7, the order approved on 25 September, have all shipped; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `234ba5b`.

## Why this comes next

The review re-ran every pack against `234ba5b`. Every pack passes its gates:

| Pack | Distinct sequences in 64 journeys | Events drawn |
|---|---|---|
| Airline | 55 | all 35 |
| Banking | 49 | all 28 |
| Hotel | 57 | all 37 |
| Insurance | 41 | all 18 |
| Telecommunications | 46 | all 34 |

The gates check each journey alone. They do not check whether a group of journeys carries a training signal, whether a narrow scope distorts outcomes, or whether an export copies what a user uploaded. Each finding below was reproduced in memory.

- **Narrow scopes favour failures.** `_choose` and `_rollouts` in `packages/sectors/src/sectors/journeys.py` (lines 360 and 471) accept a journey once it reaches the minimum length or ends on a journey-ending event. A journey that runs out of legal events in its scope before the minimum is redrawn, so the only short journeys that qualify end on a failure.
  - Airline booking alone runs `offer.viewed, order.created, payment.captured, order.confirmed` and stops, four events short of six. With a minimum of six events, it passed none of 400 sequences: every journey expired or failed payment.
  - Hotel booking alone passed 2%.
  - This is the mirror image of the success bias fixed in #34.
- **Some sub-domains carry no group signal.** Each sub-domain was run alone with 100 prompts in groups of four:

  | Sub-domain | Accepted groups | Why |
  |---|---|---|
  | Insurance quoting, billing, servicing, complaints | none | every rollout passes, so no failure is reachable in scope |
  | Telecom fault management | none | both repair paths succeed |
  | Airline loyalty | none | every rollout passes |
  | Airline booking | none | every rollout fails, because of the scope bias above |
  | Hotel check-out and billing | 4 in 100 | failures are rare |
  | Airline baggage | 8 in 100 | failures are rare |
  | Hotel booking | 8 in 100 | the scope bias above |

  Banking's lowest are cards and payments (22) and servicing (32). A group with no accepted signal gets zero advantage for every sequence, so these scopes teach nothing under group-relative rewards.
- **Exports are not checked for copies.** The standing constraints say exported data is checked for verbatim copies of uploaded records. Nothing in `apps/api/app/export.py` does so. Templated text makes a copy unlikely today; provider-written text (Slice 10) would not.
- **No CI.** The repository has no `.github/workflows`, and every pull request so far merged with no checks. The suite (272 tests) and the studio build run only where someone runs them.
- **Cost at scale is unmeasured.** For banking with groups of four, one 256-sequence batch takes:

  | Batch | Time | Estimate for 10,000 sequences |
  |---|---|---|
  | Plain | 0.18 s | about 7 s |
  | With episodes | 0.30 s | about 12 s |
  | With episodes, decision records, and the decision-score signal | 1.22 s | about 49 s |

  Most of the last figure is the decision score simulating values for every member of every group, although the members share their prefix up to the first decision.

## Slice 8: honest data at every scope

1. **Natural end.** The walker marks a walk that ran out of legal events as exhausted, and `_choose` and `_rollouts` count it as long enough, as they count a journey-ending event.
   - The composer shows each scope's typical length before generating, and warns when the minimum is beyond it. The standing constraint says caps and substitutions are shown before generation.
   - Tests: airline and hotel booking alone pass at their policies' rates, measured against the payment and guarantee outcome shares; a primary and its rollouts agree within noise.
2. **Group signal in every sub-domain.** A new gate, `group_signal`, runs each sub-domain alone in groups of four and needs accepted groups in at least a fifth of them (decision 8).
   - Where no rollout can fail, the pack gains the failure branch its industry has, for example:
     - insurance: a quote abandoned, a missed premium that lapses the policy, a claim or complaint rejected
     - telecom: a fault that recurs after repair
     - airline: missing miles that need a claim
     - hotel: a charge disputed at check-out
   - Each new branch passes the other gates, and its prior is set where the industry's rate would put it.
3. **Copies at export.** The exporter indexes 12-word runs of every uploaded document's text and every data-source row, and checks every exported text field against them (decision 9):
   - samples, prefixes, episodes, decision records, and tasks
   - a record with a match is left out
   - the manifest counts what was left out, by part
   - Tests: a planted copy is caught in a small run and in a large run's export job; a clean run leaves nothing out.
4. **CI.** A GitHub Actions workflow runs the Python suite and the studio's `next build` on every pull request and on `main`, with no secrets and the engine and providers mocked as the tests already do. The slice's own pull request is the first to show checks.
5. **Cost.** Decision values are computed once per shared prefix and reused across a group's members, since the members share their prefix up to the first decision.
   - A 10,000-sequence banking run with episodes, decision records, and the decision-score signal is measured end to end as a job, with a target of three times the plain run or less.
   - Progress messages name the stage (journeys, episodes, decisions, scoring), so a long run says what it is doing.
6. **Docs.** README, the contract (the exhausted walk, the new gate, the export check), the roadmap, and the delivered log.

### Exit criteria

- Every sub-domain of every pack, alone, yields accepted groups in at least a fifth of its groups of four, and the gate enforces it for every registered pack.
- Airline and hotel booking alone pass at their policies' rates, not near zero.
- A planted copy of an uploaded record is caught at export and counted in the manifest.
- CI passes on the slice's pull request.
- The 10,000-sequence run with every consumer feature finishes within three times the plain run.

## Queued: Slice 9, judge completion across repositories

Work in `llm_inference_engine` first (decision 7):
- a rubric registry that accepts rubrics over an authenticated API and persists them per tenant
- `/v1/evals/run` taking a scheduler slot as chat does
- the safety rubric's template including the prompt
- an `n` parameter that repeats a judgment above temperature 0
- typed errors for a timeout or an over-long prompt

Then, in the studio:
- **4B:** the judge studies a group of journeys with the warm-start passages and proposes study-specific solution and behavior rubrics. The user reviews and edits them in the judge panel before they are registered and used.
- **Repeated judgments:** each rubric is judged three times per model. Agreement across repeats is reported next to agreement across models.
- **Judge-side conformance and decision rubrics:** `process_conformance` and `decision_score` are registered with the engine so the judge can score them, and are compared with the code scorers.

Exit: a rubric proposed from a group is reviewed, registered, and used in a cycle, and agreement across repeats is reported per rubric.

## Queued: Slice 10, representative everywhere and natural text

- **Hotel calibration:** a catalogue adapter for the Hotel booking demand dataset (decision 10). Cancellations, no-shows, and lead times calibrate the hotel pack's guarantee, cancellation, and arrival outcomes and their durations.
- **Airline calibration:** an adapter for the Bureau of Transportation Statistics on-time performance data. Delay and cancellation rates and delay lengths calibrate the airline pack's disruption outcomes.
- **Telecom and insurance sources:** these follow a terms review.
- **Provider-written turn text** (decision 11): on request, a provider model on the owner's key writes each sample's turn text from the skeleton.
  - Code checks every turn: each event in order, no invented amount or identifier, and the run's language.
  - A turn that fails keeps the template.
  - The composer estimates the calls and takes a cap, as for provider rollouts.
- **More languages** (decision 12): these wait for provider-written text.

Exit: a hotel run calibrated from the catalogue reports representativeness, and provider-written turns pass the skeleton checks.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer.
