"""Retrieve one selected GRID series and its available event archive(s).

Example: python scripts/download_grid_series.py --prompt-key
The default target is Worlds 2026 Gen.G vs Invictus Gaming, November 7.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.grid_client import GridClient, GridError, load_api_key, matches_target, read_local_settings, select_event_files


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def collect(config_path, env_file, platform=None, series_id=None, prompt_key=False, events="auto", metadata_only=False, api_key=None):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    slug = config["slug"]
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise GridError("Invalid example slug.")
    key = api_key.strip() if api_key and api_key.strip() else load_api_key(env_file, prompt=prompt_key)
    if "\n" in key or "\r" in key:
        raise GridError("The configured API key contains an unexpected newline.")
    platform = platform or read_local_settings(env_file).get("GRID_PLATFORM", "open")
    client = GridClient(key, platform=platform)
    folder = ROOT / "data" / "grid" / "raw" / slug
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "collection.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {"label": config["label"], "example_slug": slug, "platform": platform,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "central_endpoint": client.central_url, "state_endpoint": client.state_url,
                "file_download_base": client.file_base, "status": "started", "files": [], "notes": []}

    def checkpoint():
        save_json(manifest_path, manifest)

    print(f"Target: {config['label']}", flush=True)
    if series_id is None:
        print("Resolving the GRID series ID from Central Data...", flush=True)
        matches, candidates = client.find_series(config)
        save_json(folder / "discovery.json", {"date_window_utc": config["date_window_utc"], "series": candidates})
        if len(matches) != 1:
            manifest["status"] = "match_resolution_required"
            manifest["matching_series_ids"] = [x["id"] for x in matches]
            checkpoint()
            raise GridError(f"Expected one visible matching LoL series; found {len(matches)}. Inspect discovery.json or supply a verified --series-id. Access restrictions can hide otherwise real matches.")
        metadata = matches[0]
        series_id = str(metadata["id"])
    else:
        metadata = client.series(str(series_id))
        if not matches_target(metadata, config):
            raise GridError("The supplied GRID series ID does not match the configured title and teams.")
        when = metadata.get("startTimeScheduled", "")
        if when and not (config["date_window_utc"]["gte"] <= when <= config["date_window_utc"]["lte"]):
            raise GridError("The supplied series is outside the configured date window; avoid selecting the October Swiss BO1.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", series_id):
        raise GridError("GRID returned an unexpected series ID format.")
    if previous.get("series_id") not in {None, series_id}:
        raise GridError("The output folder belongs to a different series. Use a distinct example slug instead of mixing files.")
    manifest["series_id"] = series_id
    manifest["status"] = "metadata_retrieved"
    save_json(folder / "series.json", metadata)
    checkpoint()
    print(f"Resolved GRID series ID: {series_id}", flush=True)

    try:
        state = client.series_state(series_id)
        save_json(folder / "series_state.json", state)
        manifest["state_retrieved"] = state is not None
        manifest["game_state_objects_returned"] = len(state.get("games", [])) if isinstance(state, dict) else 0
        manifest["notes"].append("Series State is a current/final snapshot, not the full historical event stream.")
    except GridError as error:
        manifest["state_retrieved"] = False
        manifest["notes"].append(str(error))
    checkpoint()
    if metadata_only:
        print("Saved series metadata and available final-state response; event retrieval was not requested.", flush=True)
        return folder
    try:
        listing = client.list_files(series_id)
    except GridError as error:
        manifest["status"] = "event_access_unavailable"
        manifest["event_access_error"] = str(error)
        checkpoint()
        raise GridError("Metadata was saved, but event file access failed. " + str(error), error.status) from None
    save_json(folder / "file_listing.json", listing)
    chosen = select_event_files(listing, events)
    if not chosen:
        manifest["status"] = "no_ready_event_files"
        checkpoint()
        raise GridError("No ready requested event files were listed. Check file_listing.json for processing/unavailable states; a final-state JSON is not an event substitute.")
    for info in chosen:
        print(f"Downloading listed event file: {info.get('fileName', info.get('id'))}", flush=True)
        manifest["files"].append(client.download(info, folder))
        checkpoint()
    manifest["status"] = "events_downloaded"
    checkpoint()
    print(f"Saved {len(manifest['files'])} event archive(s) under {folder.relative_to(ROOT)}", flush=True)
    print("Next: python scripts/inspect_grid_events.py", flush=True)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "data/grid/examples/worlds_2026_geng_ig.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--platform", choices=["open", "standard"])
    parser.add_argument("--series-id", help="A verified GRID series ID, not a Riot/Oracle game ID.")
    parser.add_argument("--prompt-key", action="store_true", help="Prompt for the key without echoing it if it is not configured.")
    parser.add_argument("--events", choices=["auto", "grid", "riot", "both"], default="auto")
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    try:
        collect(args.config, args.env_file, args.platform, args.series_id, args.prompt_key, args.events, args.metadata_only)
    except GridError as error:
        print(f"GRID retrieval: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
