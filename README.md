# League of Legends esports data exploration

An executed notebook examining the schema, row grain, completeness, and basic
patterns in Oracle's Elixir's newest available yearly match-data export.

- [Open the notebook](notebooks/01_oracles_elixir_schema_and_eda.ipynb)
- [Read the HTML version](reports/oracles_elixir_2026/notebook.html)
- [Read the calculated findings](reports/oracles_elixir_2026/findings.md)
- [Open the single-BO5 GRID walkthrough](notebooks/02_grid_single_bo5_event_structure.ipynb)
- [Open the GRID access, BP and gold notebook](notebooks/03_grid_free_access_bp_and_gold.ipynb)
- [Read the GRID analysis in HTML](reports/grid/free_access_3012979/notebook.html)

## Analyze the data available with the current GRID key

`notebooks/03_grid_free_access_bp_and_gold.ipynb` uses a real, expanded Series
State response for Gen.G–IG, GRID series `3012979`. The key returned all 60 draft
actions, 30 player-game summaries and six team-game summaries across three
played games. Event-file download access is unnecessary for this notebook.

Run All with the project `.venv` kernel. By default it reads the cached response
in ignored `data/grid/raw/free_access_3012979/` and makes no API calls. A blank
key therefore works when the cache exists. For a fresh snapshot, set
`REFRESH_FROM_API = True`; supply a local key, environment variable, `.env`, or
the hidden prompt. The new notebook does not copy the key from another notebook.

The walkthrough includes:

- Actual GraphQL types/descriptions, a raw-record preview, separate observation
  grains, field availability and entity/draft consistency checks before EDA.
- Champion pick/ban counts, game-level BP presence and a complete draft board.
- Final cumulative gold differences, player K/D/A and gold/vision rates, and
  listed objective totals.
- An optional **separate Oracle's Elixir** gold-difference line using audited
  10/15/20/25-minute checkpoints from the existing local CSV.

The completed-series GRID response contains final totals, not historical gold
snapshots. It cannot reconstruct an in-game gold curve or player movement.
`money`, `netWorth` and `totalMoneyEarned` are kept distinct; gold comparisons use
the last field. Roles are empty in all 30 player-game records. A Game 2 Gen.G
team/player kill discrepancy (27 versus 26) is flagged without altering the raw
values. This one series supports descriptive exploration, not causal attribution
or general champion-strength estimates.

Full tables, five figures, schema inventory and findings are exported to
`reports/grid/free_access_3012979/`. The raw folder contains the exact query,
response, provenance/hash manifest and authenticated schema introspection.
The notebook executes all 13 code cells using the existing cache, without a key
or network request. Rebuild it with:

```powershell
.\.venv\Scripts\python.exe scripts/build_grid_free_access_notebook.py
```

## Retrieve one BO5 through GRID

The GRID notebook targets the **November 7, 2026 Worlds semifinal, Gen.G vs
Invictus Gaming**. The series ended 3-0, so it contains three played games despite
its BO5 format. The October 23 Swiss BO1 is excluded by the discovery window.
The match reference and Oracle/Riot game IDs are stored separately from GRID
responses in `data/grid/examples/worlds_2026_geng_ig.json`.

1. Open `notebooks/02_grid_single_bo5_event_structure.ipynb` using the project
   `.venv` kernel.
2. Fill the deliberately blank `API_KEY = ""` locally, or use the optional hidden
   `getpass` input. Keep `GRID_PLATFORM = "open"` for an Open Access endpoint;
   use `"standard"` when your portal assigns the standard endpoints.
3. Restart and run all cells. With a blank key, the notebook skips authenticated
   requests and explains which outputs are not available yet.

The workflow resolves the **GRID series ID** using Central Data, retrieves an
available current/final state, lists downloadable files, and downloads only ready
event archives. It keeps a final-state snapshot distinct from event history.
An inaccessible semifinal, missing event entitlement, or processing file is
reported explicitly; a different match or summary is not substituted.

The parser streams JSONL members inside the ZIP. It reports nested paths/types,
real record previews, event-item counts, explicit game-ID references, parse errors,
duplicate lines, and supported transaction timestamp intervals. It initially
inspects 100,000 records; set `MAX_RECORDS = 0` for a complete scan. Numeric
timestamp units are not guessed, and transaction intervals are not interpreted
as player-position sampling frequency. GRID and Riot representations may overlap.

Raw API responses and archives are saved in Git-ignored `data/grid/raw/`.
Inspection tables are saved under `reports/grid/worlds_2026_geng_ig/`. API keys are
not printed or included in collection manifests. A key typed literally into a
notebook source cell remains there if that notebook is saved; hidden input avoids
storing it in source.

For an optional command-line workflow, configure `GRID_API_KEY` through an
environment variable or local `.env` (see `.env.example`), then run:

```powershell
.\.venv\Scripts\python.exe scripts/download_grid_series.py
.\.venv\Scripts\python.exe scripts/inspect_grid_events.py
```

Alternatively, add `--prompt-key` to the downloader for hidden input. A verified
GRID series ID can be passed as `--series-id`; Oracle/Riot IDs are not valid
substitutes. File Download uses `https://api.grid.gg/file-download`, while Open
Access metadata/state use `https://api-op.grid.gg`. Account-specific coverage
and event-file permissions still need to be verified with the user's key.

Live authentication and Series State access for series `3012979` have been
verified with the user's key. File listing returned HTTP 403 for this product
and series; that does not establish a universal rule about free versus paid
plans. Notebook 03 explores the successfully retrieved draft and final-state
data, while notebook 02 retains the event-download walkthrough.

References: [GRID Open Access quick start](https://grid.helpjuice.com/3673199),
[File Download workflow](https://grid.helpjuice.com/cloud9-x-jetbrains-hackathon/how-do-i-use-the-file-download-api),
and the [official LoL Esports schedule](https://lolesports.com/en-US/leagues/worlds,vcs).

## Data and scope

Source: [Oracle's Elixir / Tim Sevenhuysen](https://oracleselixir.com/tools/downloads),
distributed through the publisher's
[public Google Drive folder](https://drive.google.com/drive/folders/1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH).

The downloaded 2026 CSV is saved in `data/raw/`. Its adjacent `.manifest.json`
records the exact source file, download time, HTTP modification time, SHA-256,
size, and parsed row/column counts. The downloader validates the CSV before
replacing an existing local file. Large raw CSVs are excluded from Git, but remain
in the project folder.

The notebook preserves the original data. It separates player-game rows,
team-game rows, and one-observation-per-game analyses. It describes recorded
coverage; completeness against the full competition schedule is not established.
The export contains selected checkpoints, rather than continuous spatial or
action-level telemetry.

## Notebook contents

1. Snapshot provenance and file verification.
   The four-part completeness audit runs immediately afterward, before gameplay
   summaries or figures: schema contracts, source-related coverage, classified
   missingness, and player/team alignment.
2. Full column inventory with types, examples, feature families, and row-type availability.
3. Game grain, participant slots, and a twelve-row example game.
4. Identifier, outcome, arithmetic, and duration-unit checks.
5. Recorded coverage by league and month.
6. Missingness and eligible-game checkpoint availability at 10/15/20/25 minutes.
7. Explicit EDA populations and filtering counts.
8. Duration and combat pace.
9. Blue-side win rates with descriptive uncertainty intervals.
10. Gold advantage at 15 minutes and final results.
11. Wins from an early gold deficit.
12. Player resource and damage distributions by role.
13. Calculated findings, limitations, and execution environment.

Explanations, code annotations, section titles, and chart labels are in English.
The default detailed EDA cohort is LCK; change `FOCUS_LEAGUE` in the first code
cell to explore another league.

## Completeness audit

- **Sources:** compare league, month/patch, player/team grain, publisher flag, and
  reference-link hostname. The CSV does not establish the upstream provider for
  every row. A missing URL is not evidence that gameplay statistics are absent.
- **Missing-value types:** distinguish observed zeros, explicit structural
  exclusions, checkpoints not reached, absent columns, unknown eligibility,
  unexpected gaps, and invalid or contradictory values. No nulls are replaced
  with zero. Bounded missing runs are investigation candidates, not verified outages.
  Values supplied for games ending before a checkpoint are reported separately;
  agreement with final totals identifies possible end-capped values, which are
  excluded from true checkpoint analyses without modifying the raw file.
- **Versions:** compare with the pinned, reviewed 2026 contract in
  `data/contracts/`. Flag new/missing columns, numeric type changes, and ambiguous
  legacy aliases without silently renaming fields. Historical header retrieval
  hit Google Drive's download quota, so cross-year schema equivalence remains
  unverified. Equal column names alone do not establish equal historical units.
- **Entities:** use validated non-null `(gameid, side)` keys for many-to-one
  player-to-team joins, then compare rosters, IDs, names, and results. Keep missing
  persistent IDs and name collisions separate from local game-level eligibility.

The audit exports full field/league/grain missingness, source and temporal
coverage, schema differences, entity review rows, bounded-run candidates, and
analysis-specific sample counts. Blocking schema, key, or numeric failures stop
the notebook before gameplay EDA. The original consistency checks and six figures
remain available after the audit.

`analysis/completeness.py` contains the pandas audit functions. Regression tests
cover cutoff boundaries, zero preservation, missing columns, aliases, unknown
context, null keys, duplicate team rows, and transfers/name collisions:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Run locally

Use Python 3.12 and a project environment. In PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
```

Open the notebook in a notebook editor such as VS Code, select the project
`.venv` interpreter, then run all cells. The project root and `notebooks/` are
both supported working directories. Analysis uses the local file without
requesting network access or downloading again.

To rebuild the notebook, execute every cell, and export an HTML report:

```powershell
.\.venv\Scripts\python.exe scripts/build_eda_notebook.py
```

The builder uses project-local Jupyter configuration and a temporary kernel
definition. It does not install a user-wide kernel. Generated tables, six figures,
findings, source-linked summary, and environment versions are saved under
`reports/oracles_elixir_2026/`.

## Refresh the raw snapshot

The downloader needs internet access and only the Python standard library:

```powershell
py -3.12 scripts/download_oracles_elixir.py
```

It selects the newest season visible in the publisher's public folder. To select
a particular available season, add `--year 2026`. Re-run the notebook after any
refresh; its embedded outputs and reports describe the previous snapshot until
then. Google Drive quota or page-format errors stop the download without
replacing an existing CSV. The export's schema may change across seasons.

For a header-only version reference, use `--schema-only --year 2025`. This reads
only an initial response chunk and saves column names and source metadata in
`data/schema_snapshots/`; it does not audit historical values or units. Download
quotas can also prevent header retrieval.
