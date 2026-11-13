# Oracle's Elixir 2026: observed findings

- Snapshot: **9,915 recorded games**, **118,980 rows**, **165 columns**, **44 league labels**.
- Recorded date range: **2026-01-01 09:14:49 to 2026-11-12 17:11:38** (source timezone unverified).
- Row grain: **9,915/9,915 games** have ten player rows and two team summaries with the expected roles and sides.
- Time resolution: gold/XP/CS and combat checkpoints at **10, 15, 20, 25 minutes**; this file does not supply continuous positions or a fight event sequence.
- Gold difference at 15: **9,740/9,914 eligible games** have a value (**98.24%**). Missing eligible games by league: **LPL: 132, DCGI: 32, WLRQ: 6, DCup: 3, LEC: 1**.
- Identity gaps: **1,913 player rows** lack player IDs; **552 team rows** lack team IDs. These matter for history joins even when game totals are usable.
- LCK EDA: **502 games**, median duration **31.28 minutes**, observed Blue win rate **56.4%**.
- Under the explicit early-deficit definition, **142/502** LCK games with a trailing team at 15:00 were eventually won by that team.

## What this snapshot supports

Recorded-coverage auditing, row-type-aware missingness, role and resource profiles, game-duration and pace comparisons, and descriptive early-lead conversion.

## What remains unresolved

External schedule coverage has not been checked. A publisher `complete` flag does not promise all columns are populated.
Late checkpoints select games that lasted that long. Side, gold and resource associations do not establish causal effects.
Individual tactical responsibility needs richer action and spatial evidence.

## Four-part completeness audit

The pinned contract covers **165 fields**. Schema checks distinguish absent columns, numeric type changes and legacy aliases requiring review.
Historical header comparison was unavailable because of the public Google Drive download quota. No cross-year equivalence is asserted.
Source coverage is reported by league, row grain, publisher flag and reference-link hostname. The CSV does not verify a provider for every row.
Missing cells are separated into explicit structural exclusions, unknown eligibility, absent columns and unexpected gaps. Observed zeros remain unchanged.
**441 games** ended before 25:00 but have supplied gold-at-25 values;
**441 of those values equal final gold totals**. They are excluded from true 25-minute checkpoint analyses.
The entity audit found **0 blocking alignment issues**, **19 name/ID collision review items**,
and **70 field-specific bounded missing-run candidates**. These runs are not confirmed outages.
Full details and analysis-specific sample sizes are in the audit tables exported alongside this report.

## Source and reproducibility

Publisher: [Oracle's Elixir / Tim Sevenhuysen](https://oracleselixir.com/tools/downloads).
[Exact source file](https://drive.google.com/file/d/1hnpbrUpBMS1TZI7IovfpKeZfWJH1Aptm/view). Retrieved UTC: 2026-11-13T04:40:19.457522+00:00.
SHA-256: `528c0f20fb940319076270051763cca5b55e777b8f3343ef988c789c8accd239`.
