"""Inspect downloaded event archives; no API key or network access is needed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.grid_events import inspect_archives


def inspect_example(slug="worlds_2026_geng_ig", max_records=100000):
    raw_folder = ROOT / "data" / "grid" / "raw" / slug
    manifest_path = raw_folder / "collection.json"
    if not manifest_path.exists():
        raise FileNotFoundError("No GRID collection exists yet. Run the download notebook or download_grid_series.py first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if not files:
        raise FileNotFoundError("No event archives were downloaded. Inspect collection status and the file listing; metadata alone is not an event stream.")
    paths = [raw_folder / f["filename"] for f in files]
    if not all(p.is_file() for p in paths):
        raise FileNotFoundError("A listed event archive is missing from the raw folder.")
    report_folder = ROOT / "reports" / "grid" / slug
    summary = inspect_archives(paths, report_folder, max_records=max_records)
    expected = {f["filename"]:f["sha256"] for f in files}
    if any(f["sha256"] != expected[f["filename"]] for f in summary["source_files"]):
        raise ValueError("An event archive differs from its download manifest. Review the raw files before interpreting outputs.")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="worlds_2026_geng_ig")
    parser.add_argument("--max-records", type=int, default=100000, help="Inspection limit across files; 0 scans all records.")
    args = parser.parse_args()
    if args.max_records < 0:
        parser.error("--max-records must be nonnegative")
    try:
        print(json.dumps(inspect_example(args.slug, args.max_records), indent=2))
    except (FileNotFoundError, ValueError) as error:
        print(f"GRID inspection: {error}", file=sys.stderr)
        raise SystemExit(2)
