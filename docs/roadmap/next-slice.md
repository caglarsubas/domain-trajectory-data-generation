# Next slice: a fair realism comparison

Status: approved on 29 September 2026, together with the queued Slice 19 and decisions 25 and 26 in the [overview](overview.md#decisions), all as recommended; Slice 18 has shipped. Slices 16 and 17, approved on 29 September, shipped before them; that plan and its evidence are in git history, and [delivered.md](delivered.md) lists each pull request.

Branch off `main` at `e00c382`.

## Why this comes next

The review re-ran every gate against `e00c382`, and every pack passes all nine, with the same measures as at `74ae828`:

| Pack | Distinct sequences in 64 journeys | Distinct sentences, English and Turkish | Lowest sub-domain's accepted groups |
|---|---|---|---|
| Airline | 57 | 62% | check-in and boarding, 32% |
| Banking | 50 | 60% | cards and payments, 30% |
| Hotel | 55 | 53% | in-stay services, 27% |
| Insurance | 43 | 53% | servicing, 32% |
| Telecommunications | 51 | 56% | activation and porting, 27% |

A 10,000-sequence banking run costs what it did at `74ae828`. Timed on the same machine, the two commits interleaved, it took 4.6 to 5.0 seconds plain against 4.7 to 4.8, and 6.8 to 7.7 with episodes, decision records, and the decision-score signal against 7.1 to 7.5. CI passes on `main`, and no pull request is open. The studio's `.env` now points at the host's engine, so the Compose stack reaches the judge; the running stack predates #72 and needs `docker compose up -d --build` to show the judge check.

This review asked whether the one measure where a judge compares, generated against real, says what it appears to say. The judges' reasons led to a defect in the data itself.

- **The realism comparison detects a broken journey.** Live on the local engine, 16 hotel journeys per run, over booking, changes and cancellations, and arrival, were each set against a real case from the hotel booking demand data, in both orders, once at temperature 0 (192 verdicts, none unreadable, 7 minutes with the two judges in parallel). As a control, each calibrated journey was also shown out of order: its first recorded step moved to the end, its times kept.

  | Hotel run | `qwen3.6:27b` picked the real case | `gemma4:26b` |
  |---|---|---|
  | Calibrated from the catalogue | 69% of 32 | 66% of 32 |
  | Uncalibrated | 75% of 32 | 53% of 32 |
  | Out of order, the control | 97% of 32 | 91% of 32 |

  Both judges caught nearly every out-of-order journey, and kept their pick when the order was swapped in 94% and 81% of those pairs. On the other two runs they kept it in 50% to 81%, as a judge that cannot tell does. Between the calibrated and uncalibrated runs, the judges moved 6 and 13 points in opposite directions, within the noise of 32 comparisons: calibration made no difference they could see.
- **Part of what the comparison reads is not the journey.** Where the judges picked the real case, their reasons, counted by keyword, cite three tells:

  | Tell the reason cites | Calibrated run | Uncalibrated run |
  |---|---|---|
  | The generated journey stops before checkout, or is incomplete | 11 of 43 | 21 of 41 |
  | A gap of hundreds of days is improbable | 21 of 43 | 4 of 41 |
  | Times too precise, or missing | 9 of 43 | 3 of 41 |

  The first and third are the comparison's own doing. 13 of the 24 real cases include checkout, but a run scoped to booking, changes, and arrival never reaches it, because real cases are cut to the events the data records and not to the study's scope. And the data records dates, so every real time is a whole number of days, while 90% to 95% of the generated times after the first step are not.
- **The long gaps are the generator's.** In a calibrated hotel run, the walker draws 14% of the waits from booking to arrival at exactly 406 days, and 33% of the waits from booking to cancellation at exactly 334 days. Calibration keeps each step's wait as three quantiles, the 10th, 50th, and 90th percentiles. The walker draws from a log-normal fitted to them and clamps the draw at twice the 90th percentile. Where the data's waits are skewed, with many near zero, the fitted spread is wide, and the tail beyond the clamp lands on that one value:

  | Timed step | Data median | Data 90th percentile | Drawn above it (data: 10%) | Drawn at exactly twice it |
  |---|---|---|---|---|
  | Hotel: booking to cancellation | 25 days | 167 days | 38% | 33% |
  | Hotel: booking to arrival | 42 days | 203 days | 22% | 14% |
  | BPI 2017: KYC started to review required | 0.9 days | 5.0 days | 29% | 22% |
  | BPI 2017: review required to approved | 3.7 days | 15.7 days | 23% | 13% |

  Medians match; the tails are two to four times too heavy. In BPI 2017, 14 of the 16 timed steps put 5% or more of their draws on the clamp, and 10 of them 10% or more. Nothing reported it, because representativeness measures next steps (fitness, precision, and next-step divergence), not waits.

So the waits of every calibrated run are less representative than the next steps it reports, and the realism comparison mixes that with tells of its own.

## Next: Slice 19, a fair realism comparison

1. **The same scope on both sides** (decision 26). A real case is cut to the events of the study's own sub-domains before it is shown, as the generated journey is cut to the events the data records.
2. **The same resolution on both sides.** Calibration records the resolution of a source's times, such as whole days for a source that records dates, and both sides are drawn at it.
3. **A control for realism.** Each cycle also sets out-of-order copies of generated journeys against real cases. A judge that does not pick the real case over the control in three of four comparisons is flagged as blind to realism, and the run page does not read its rate as a finding.
4. **Enough comparisons to read.** Realism draws its own sample of generated journeys, 16 by default, each set against a different real case, apart from the journeys the rubrics are asked about.

Exit: in live cycles on hotel runs scoped to booking, changes and cancellations, and arrival, calibrated and uncalibrated, each judge catches the out-of-order control in three of four comparisons or more; no reason cites a step outside the study's scope or a time finer than a day; and each judge's rate over 16 comparisons is reported for both runs.

Considered and not proposed: telling the judge what the data says, such as how far ahead bookings are made. The code already compares those statistics, and after Slice 18 it will compare waits too; putting them in the question would make the comparison less blind.

## Delivered: Slice 18, waits as the data has them

Delivered in one pull request covering tasks 1 to 3, and the exit criterion is met.

1. **Waits from the data's own distribution** (decision 25). Calibration keeps each timed step's waits as 21 quantiles, every 5% of its sample. The walker draws a wait by interpolating between them, so no wait falls outside the range the data shows, and none piles up on one value.
2. **Stored calibrations without a spike.** A calibration stored with three quantiles draws within the range they span, with tails bounded by the data's own spread instead of clamped, until its source is calibrated again.
3. **Waits measured.** Representativeness reports, for each timed step the data backs, how far the generated waits lie from the data's: the share above the data's 90th percentile, and a distance over the quantiles. The run page shows the steps furthest off.

Exit: in calibrated hotel and banking runs, no single wait holds more than 1% of a step's draws; every timed step the data backs with enough cases puts between 5% and 15% of its draws above the data's 90th percentile; and the quality report shows each step's distance.

Measured on calibrated runs of 1,500 journeys over every sub-domain, two seeds each, hotel from the booking demand data and banking from BPI 2017, with the walker before this slice patched back in for comparison:

| Timed step | Largest share on one value, before → after | Past the data's 90th percentile, before → after |
|---|---|---|
| Hotel: booking to arrival | 12% to 13% → 0.1% | 21% to 22% → 10% to 11% |
| Hotel: arrival to departure | 2.0% to 2.4% → 0.3% | 10% to 11% → 5% to 7% |
| BPI 2017: submitted to KYC started | 0.9% to 1.2% → 0.1% | 4% to 5% → 8% to 11% |
| BPI 2017: KYC started to review required | 21% to 24% → 0.3% | 29% to 31% → 8% to 10% |
| BPI 2017: submitted to abandoned | 5% to 6% → 0.2% | 34% to 37% → 11% to 13% |
| BPI 2017: started to abandoned | 11% to 15% → 0.5% to 0.6% | 39% to 45% → 11% to 12% |

The run's wait distance fell from 0.24 to 0.31 to 0.04 to 0.13. Every timed step with at least 25 cases in the data and 100 draws lands within 5% to 15% of its draws past the data's 90th percentile; where the data records dates, as the hotel data does, generated waits are read between dates too, and its many one-night stays tie at the 90th percentile, which puts departures at the low end. A step drawn fewer than 100 times, such as BPI's review to abandonment, cannot show a share below 1% at all, and its distance, 0.27, is mostly sampling noise. Calibrations stored before the slice, with three quantiles, draw as cleanly: at most 0.6% on one value, and 8% to 13% past the 90th percentile.

Found on the way: a calibrated run large enough to be written in batches recorded an event name in place of each batch's name, because counting its second-order steps reused the batch's variable, so its journeys could not be opened and anything reading its batches by name missed them. Fixed, with a test that opens one.

## Out of scope

New sectors beyond the five, a trainer, and hosting. Jev-type remains a target family on the same contract, not a trainer. A third language waits for a study that asks for one (decision 17).
