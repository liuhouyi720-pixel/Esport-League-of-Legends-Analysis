"""Build the single-BO5 GRID walkthrough with an intentionally blank API key."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
for variable, relative in {
    "TEMP": ".cache/tmp", "TMP": ".cache/tmp", "JUPYTER_RUNTIME_DIR": ".cache/jupyter/runtime",
    "JUPYTER_DATA_DIR": ".cache/jupyter/data", "IPYTHONDIR": ".cache/ipython", "MPLCONFIGDIR": ".cache/matplotlib",
}.items():
    folder = ROOT / relative
    folder.mkdir(parents=True, exist_ok=True)
    os.environ[variable] = str(folder)

import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager


def main():
    cells = []
    def md(source):
        cells.append(nbformat.v4.new_markdown_cell(textwrap.dedent(source).strip()))
    def code(source):
        cells.append(nbformat.v4.new_code_cell(textwrap.dedent(source).strip()))

    md("""
    # One BO5 with GRID: Worlds 2026 Gen.G vs Invictus Gaming

    Retrieve one selected series, preserve its original event archive, and inspect the actual schema and timing.
    **The API key is intentionally blank.** Fill it locally in the next cell, then Restart & Run All.
    With a blank key and no cached data, authenticated requests and data analysis are skipped with clear status messages.
    No sample results are invented.

    **Selected match:** Worlds 2026 semifinal on November 7, Gen.G 3-0 Invictus Gaming.
    A BO5 can finish in three games; do not expect five played-game files. The October 23 BO1 between these teams
    is a separate series. [Official schedule](https://lolesports.com/en-US/leagues/worlds,vcs).

    **Workflow:** identify the GRID series ID through Central Data, retrieve its state, list available files,
    download a ready event file, then inspect JSONL records and their nested events offline.

    **Access matters:** a metadata/state key does not automatically enable event downloads. A 403 is an access result,
    not a reason to replace event history with a final-state snapshot. This specific semifinal may be outside your account's
    historical coverage. The notebook reports that outcome instead of silently choosing another match.

    Sources: [Central Data quick start](https://grid.helpjuice.com/3673199),
    [File Download workflow and its different API hostname](https://grid.helpjuice.com/cloud9-x-jetbrains-hackathon/how-do-i-use-the-file-download-api),
    [GRID file formats](https://docs.grid.gg/public/documentation/api-documentation/in-game-data/grid-file-download-api).
    """)

    md("""
    ## 1. Enter local settings

    Enter the key only on your computer. It is sent in the `x-api-key` header, never as a query-string parameter.
    The optional `getpass` line accepts hidden input without putting the key in notebook source.
    A literal you type into `API_KEY` remains in the notebook source if you save the file; hidden input avoids that.
    This notebook does not print the key or write it into downloaded metadata or reports.

    `GRID_PLATFORM = 'open'` selects `api-op.grid.gg` for Central Data and Series State.
    Use `'standard'` if your portal assigns `api.grid.gg` for those products.
    File Download uses **`api.grid.gg/file-download`** in both configurations.
    Leave `SERIES_ID` empty to resolve the selected match by date, title and both teams.
    Only enter a verified GRID series ID here; Oracle/Riot game IDs use a different namespace.
    """)
    code(r'''
    from pathlib import Path
    import getpass
    import json
    import sys
    import textwrap
    import pandas as pd
    import matplotlib.pyplot as plt
    from IPython.display import display, Markdown, JSON
    %matplotlib inline

    API_KEY = ""
    # Optional alternative: uncomment the next line for hidden input.
    # API_KEY = getpass.getpass("GRID API key (hidden): ")
    GRID_PLATFORM = "open"
    SERIES_ID = ""
    EVENT_FORMAT = "auto"
    MAX_RECORDS = 100_000
    # MAX_RECORDS = 0 scans every JSONL record instead of the initial inspection sample.

    ROOT = next((p for p in [Path.cwd(), *Path.cwd().parents]
                 if (p / "data/grid/examples/worlds_2026_geng_ig.json").is_file()), None)
    if ROOT is None:
        raise FileNotFoundError("Open this notebook from the project root or notebooks/.")
    for path in [ROOT, ROOT / "scripts"]:
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from analysis.grid_client import GridError
    from analysis.grid_events import preview
    from download_grid_series import collect
    from inspect_grid_events import inspect_example
    CONFIG_PATH = ROOT / "data/grid/examples/worlds_2026_geng_ig.json"
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    RAW_DIR = ROOT / "data/grid/raw" / config["slug"]
    REPORT_DIR = ROOT / "reports/grid" / config["slug"]
    pd.set_option("display.max_colwidth", 100)
    pd.set_option("display.max_rows", 20)
    display(pd.Series({"Example":config["label"], "Platform":GRID_PLATFORM,
                       "Key entered":bool(API_KEY.strip()), "Inspection record limit":MAX_RECORDS or "all records"}).to_frame("Setting"))
    ''')

    md("""
    ## 2. Confirm the series reference and ID namespaces

    The reference below comes from the official schedule and the Oracle's Elixir snapshot already in this project.
    It is **not a GRID response**. The downloader resolves a new GRID series ID using a narrow November date window,
    League of Legends as the title, and both team names. It stops on zero or multiple matches.

    | Layer | Meaning |
    |---|---|
    | GRID series | The complete BO5 matchup |
    | Played game | One of the three games actually played |
    | JSONL record | Often a transaction containing one or more events in the GRID format |
    | Event item | An individual event inside that record |

    Riot-specific files can use a different structure. The inspection discovers the structure present in your files.
    """)
    code(r'''
    display(pd.Series({"Reference format":config["format_reference"], "Reference result":config["result_reference"],
                       "Reference played games":config["expected_played_games_reference"],
                       "GRID series ID":SERIES_ID or "Resolve through Central Data",
                       "Discovery from (UTC)":config["date_window_utc"]["gte"],
                       "Discovery through (UTC)":config["date_window_utc"]["lte"]}).to_frame("Reference only"))
    display(pd.DataFrame({"Reference game number":[1,2,3],
                          "Oracle/Riot game ID (not a GRID series ID)":config["oracle_game_ids_reference"]}))
    ''')

    md("""
    ## 3. Authenticate, resolve the series, and download event files

    This cell makes requests **only when API_KEY is nonempty**. Central Data uses GraphQL over HTTP POST.
    The File Download API first lists files through `GET /file-download/list/{seriesId}`.
    Download the returned `fullURL` only when its status is `ready`.

    `auto` prefers the GRID event archive and otherwise selects Riot event archives. Use `grid`, `riot` or `both`
    to request a particular representation. When comparing both representations, their counts are not independent
    observations of different matches. Missing, processing, unauthorized, or unavailable files are reported explicitly.

    Raw API responses and archives stay in the Git-ignored `data/grid/raw/` directory. The collection manifest records
    the actual series ID, retrieval time, file sizes, SHA-256 values, and access outcome without storing credentials.
    """)
    code(r'''
    if API_KEY.strip():
        try:
            collect(CONFIG_PATH, ROOT / ".env", platform=GRID_PLATFORM, series_id=SERIES_ID or None,
                    events=EVENT_FORMAT, api_key=API_KEY)
            display(Markdown("**Retrieval completed.** Continue with the local archive inspection below."))
        except GridError as error:
            display(Markdown("**Retrieval did not produce a ready event archive:** " + str(error)))
            display(Markdown("Check the platform, visible series, enabled products, and file status. A current/final state is not an event history."))
    else:
        display(Markdown("**API_KEY is blank: authenticated retrieval skipped.** Enter the key locally and rerun, or inspect an existing cached collection below."))
    ''')

    md("""
    ## 4. Inspect the actual access result and available files

    Read the saved collection status before interpreting data. A successful Central Data query does not prove access
    to File Download, and a successful file-list request does not guarantee the requested file is ready.
    The table displays file descriptions/statuses, not credential-bearing request headers.
    """)
    code(r'''
    collection_path = RAW_DIR / "collection.json"
    collection = json.loads(collection_path.read_text(encoding="utf-8")) if collection_path.exists() else None
    if collection:
        display(pd.Series({k:collection.get(k) for k in ["label", "series_id", "status", "retrieved_at_utc", "state_retrieved", "game_state_objects_returned"]}).to_frame("Observed collection"))
        if collection.get("event_access_error"):
            display(Markdown("**Event access:** " + collection["event_access_error"]))
        listing_path = RAW_DIR / "file_listing.json"
        if listing_path.exists():
            listing = pd.DataFrame(json.loads(listing_path.read_text(encoding="utf-8")).get("files", []))
            if len(listing):
                display(listing.reindex(columns=["id", "description", "status", "fileName"]))
        display(pd.DataFrame(collection.get("files", [])))
    else:
        display(Markdown("**No GRID collection has been retrieved yet.** The reference match information above is not API data."))
    ''')

    md("""
    ## 5. Inspect a final-state response separately from event history

    A state response is useful for checking series/game/team/player nesting. It cannot by itself reconstruct all
    intermediate decisions in a completed match. The preview below limits nested arrays and clearly marks omitted items;
    the original saved JSON remains unchanged. Count returned game-state objects separately from verified played games.
    """)
    code(r'''
    state_path = RAW_DIR / "series_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        display(JSON(preview(state), expanded=False))
        if isinstance(state, dict):
            display(pd.Series({"Root fields":list(state), "Returned game-state objects":len(state.get("games", []))}).to_frame("Observed state"))
    else:
        display(Markdown("No state response is cached yet."))
    ''')

    md("""
    ## 6. Stream the downloaded JSONL archive for a first inspection

    JSONL stores one JSON value per line. A GRID transaction can contain multiple event items, so raw record count,
    event-item count and played-game count have different grains. Stream the ZIP members without extracting paths
    or loading the entire series into memory.

    The initial limit is 100,000 valid records across the selected files. If the limit is reached, all counts and schema
    presence percentages describe that inspected subset. Set `MAX_RECORDS = 0` for a full scan. Parse errors, duplicate
    lines, uninspected members and unknown event shapes remain visible. The parser does not invent event categories.
    """)
    code(r'''
    inspection = None
    if collection and collection.get("files"):
        inspection = inspect_example(config["slug"], MAX_RECORDS)
        display(pd.Series({k:v for k,v in inspection.items() if k!="source_files"}).to_frame("Observed inspection"))
        display(pd.read_csv(REPORT_DIR / "archive_members.csv"))
    else:
        display(Markdown("**No event archives to inspect yet.** This cell runs after a successful event download."))
    ''')

    md("""
    ## 7. Discover schema paths, event types, and raw examples

    `schema_paths.csv` reports JSON paths, types, examples, occurrence counts, and record-level presence within each
    archive member. A path ending in `[]` denotes array items; repeated items count as multiple occurrences but only
    one record-level presence. Explore actual actor, target, game-ID, clock and position fields before flattening them.

    Event labels come from explicit `events` arrays or event-like objects. Unrecognized formats remain unrecognized
    until reviewed. The displayed previews are truncated views of real records; no synthetic payloads are used here.
    """)
    code(r'''
    if inspection:
        paths = pd.read_csv(REPORT_DIR / "schema_paths.csv")
        types = pd.read_csv(REPORT_DIR / "event_type_counts.csv")
        display(paths.head(25))
        useful = paths["path"].str.contains("actor|target|position|clock|time|gameId", case=False, regex=True)
        display(paths.loc[useful, ["path", "json_type", "presence_pct", "example"]].head(35))
        display(types.sort_values("event_items", ascending=False).head(25))
        display(JSON(json.loads((REPORT_DIR / "record_previews.json").read_text(encoding="utf-8")), expanded=False))
        if len(types):
            top = types.groupby("event_type")["event_items"].sum().nlargest(15).sort_values()
            fig, ax = plt.subplots(figsize=(11, max(4, len(top)*.38)), layout="constrained")
            ax.barh([textwrap.fill(str(x), 50) for x in top.index], top.values, color="#3563B4")
            ax.set(xlabel="Event items in the inspected records", title="Observed event-type distribution (inspection subset)")
            fig.savefig(REPORT_DIR / "event_type_distribution.png", dpi=150, bbox_inches="tight")
            plt.show()
            plt.close(fig)
    else:
        display(Markdown("Schema paths and event examples appear here after a real archive is downloaded and inspected."))
    ''')

    md("""
    ## 8. Examine timing without assuming a fixed sampling frequency

    The timing report currently parses **timezone-aware ISO timestamps at the root of each record**.
    Numeric timestamps, game clocks, or nested timestamp paths need schema-specific unit verification before use.
    A timestamp's decimal precision does not establish how often positions or health were updated.

    Adjacent transaction-time differences describe irregular event transactions. Equal timestamps can be legitimate
    batching; backward steps need investigation. Inter-game breaks can appear as large gaps. None of these numbers
    automatically represents player-position update frequency, game simulation tick rate, or network polling frequency.
    Position granularity should be measured separately after identifying actual coordinate updates and game clocks.
    """)
    code(r'''
    if inspection:
        timing = pd.read_csv(REPORT_DIR / "transaction_timestamp_audit.csv")
        display(timing)
        if timing.empty:
            display(Markdown("No supported root-level ISO timestamp field was identified. Inspect the raw clock paths and confirm units before calculating cadence."))
        references = pd.read_csv(REPORT_DIR / "explicit_game_id_references.csv")
        display(references)
        display(pd.read_csv(REPORT_DIR / "parse_errors.csv"))
        display(Markdown("**Explicit game-ID field references are not yet normalized event-to-game assignments.** Compare namespaces and validate game boundaries before joining to Oracle's Elixir."))
    else:
        display(Markdown("No event-level timing measurement is available until the real data is retrieved."))
    ''')

    md("""
    ## 9. What to establish from this one series

    After running with your key, answer these questions from the returned data:

    1. Was the intended November semifinal visible, and what GRID series ID was returned?
    2. Did the key authorize event downloads or only metadata/state?
    3. Which event types and game IDs actually appear? How are transactions and events nested?
    4. Are there usable per-player coordinates, health, items or objective events, and at what measured resolution?
    5. What portion of the series was inspected, and are there malformed records, duplicates or timing gaps?

    A blank-key execution verifies notebook structure and the skipped-request path only. It does not verify live API
    entitlements, the current GraphQL schema, event availability, or the series' actual event granularity.
    """)

    notebook = nbformat.v4.new_notebook(cells=cells, metadata={
        "kernelspec": {"display_name":"Python (project .venv)", "language":"python", "name":"python3"},
        "language_info": {"name":"python", "version":"3.12"},
    })
    output = ROOT / "notebooks/02_grid_single_bo5_event_structure.ipynb"
    output.parent.mkdir(parents=True, exist_ok=True)
    nbformat.validate(notebook)
    nbformat.write(notebook, output)
    kernel_root = ROOT / ".cache/kernels"
    kernel_dir = kernel_root / "grid-inspection"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    (kernel_dir/"kernel.json").write_text(json.dumps({
        "argv":[sys.executable,"-m","ipykernel_launcher","-f","{connection_file}"],
        "display_name":"GRID notebook inspection", "language":"python"}), encoding="utf-8")
    manager = KernelManager(kernel_name="grid-inspection", kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(kernel_root)]))
    client = NotebookClient(notebook, km=manager, timeout=300, startup_timeout=45,
                            resources={"metadata":{"path":str(ROOT)}}, allow_errors=False)
    try:
        client.execute()
    finally:
        nbformat.write(notebook, output)
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        if client.kc is not None:
            client.kc.stop_channels()
    nbformat.validate(notebook)
    print(f"Saved and executed blank-key walkthrough: {output}", flush=True)


if __name__ == "__main__":
    main()
