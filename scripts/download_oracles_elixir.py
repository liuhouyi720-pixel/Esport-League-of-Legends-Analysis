"""Download the newest Oracle's Elixir yearly CSV from its public Drive folder.

Uses only the Python standard library. Existing snapshots are replaced only
after the response has been checked as a parseable, nonempty match-data CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, parse_qsl
from urllib.request import build_opener, HTTPCookieProcessor, Request


ROOT = Path(__file__).resolve().parents[1]
FOLDER_URL = "https://drive.google.com/drive/folders/1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH"
DOWNLOADS_PAGE = "https://oracleselixir.com/tools/downloads"
NAME_PATTERN = re.compile(r"^(20\d{2})_LoL_esports_match_data_from_OraclesElixir\.csv")


class DrivePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = {}
        self.form_action = None
        self.form_values = {}
        self.in_download_form = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        label = attrs.get("aria-label", "")
        match = NAME_PATTERN.match(label)
        if match:
            file_id = attrs.get("data-id")
            embedded = re.search(r":([A-Za-z0-9_-]{20,})-0-\d+$", attrs.get("ssk", ""))
            if not file_id and embedded:
                file_id = embedded.group(1)
            if file_id:
                self.files[match.group(0)] = {"id": file_id, "year": int(match.group(1))}
        if tag == "form" and attrs.get("id") == "download-form":
            self.in_download_form = True
            self.form_action = attrs.get("action")
        if tag == "input" and self.in_download_form and attrs.get("name"):
            self.form_values[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_download_form = False


def fetch(opener, url):
    return opener.open(Request(url, headers={"User-Agent": "OracleElixirResearchNotebook/1.0"}), timeout=60)


def download(year=None, output_dir=None, schema_only=False):
    output_dir = Path(output_dir) if output_dir else ROOT / "data" / ("schema_snapshots" if schema_only else "raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with fetch(opener, FOLDER_URL) as response:
        listing = DrivePage()
        listing.feed(response.read().decode("utf-8"))
    candidates = [(name, info) for name, info in listing.files.items() if year is None or info["year"] == year]
    if not candidates:
        raise RuntimeError("No requested yearly CSV found in the public folder; its page format may have changed.")
    filename, info = max(candidates, key=lambda item: item[1]["year"])
    print(f"Newest selected season: {info['year']}; available seasons: {sorted({x['year'] for x in listing.files.values()})}", flush=True)
    url = "https://drive.usercontent.google.com/download?" + urlencode({"id": info["id"], "export": "download", "confirm": "t"})
    target = output_dir / filename
    temporary = target.with_suffix(".csv.part")
    try:
        for attempt in range(2):
            with fetch(opener, url) as response:
                first = response.read(65536)
                if first.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    html = first + response.read()
                    if b"quota" in html.lower() or b"too many users" in html.lower():
                        raise RuntimeError("Google Drive download quota exceeded; no data file was replaced.")
                    confirmation = DrivePage()
                    confirmation.feed(html.decode("utf-8", errors="replace"))
                    if attempt == 0 and confirmation.form_action:
                        action = urlparse(confirmation.form_action)
                        if action.scheme != "https" or action.netloc not in {"drive.usercontent.google.com", "drive.google.com"}:
                            raise RuntimeError("Unexpected download confirmation host.")
                        params = dict(parse_qsl(action.query)) | confirmation.form_values
                        url = action._replace(query=urlencode(params)).geturl()
                        continue
                    raise RuntimeError("Received an HTML page instead of the dataset.")
                if not first.decode("utf-8-sig", errors="replace").startswith("gameid,"):
                    raise RuntimeError("Response does not start with the expected Oracle's Elixir CSV header.")
                if schema_only:
                    header = first.splitlines()[0].decode("utf-8-sig")
                    columns = next(csv.reader([header]))
                    if "participantid" not in columns or len(columns) != len(set(columns)):
                        raise RuntimeError("Invalid schema header.")
                    schema = {
                        "season": info["year"], "filename": filename,
                        "file_url": f"https://drive.google.com/file/d/{info['id']}/view",
                        "folder_url": FOLDER_URL,
                        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                        "columns": columns, "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                        "scope": "Header-only retrieval; no historical rows, units, or field availability were audited.",
                    }
                    destination = output_dir / f"{info['year']}.schema.json"
                    destination.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
                    print(f"Saved {len(columns)} column names to {destination}", flush=True)
                    return destination
                digest = hashlib.sha256()
                with temporary.open("wb") as handle:
                    handle.write(first)
                    digest.update(first)
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
                headers = dict(response.headers)
                resolved_url = response.geturl()
                break
        with temporary.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            columns = next(reader)
            required = {"gameid", "league", "position", "side", "result", "gamelength"}
            if not required.issubset(columns) or len(columns) != len(set(columns)):
                raise RuntimeError("CSV schema validation failed.")
            rows = 0
            for line, row in enumerate(reader, start=2):
                if len(row) != len(columns):
                    raise RuntimeError(f"Malformed CSV record at row {line}.")
                rows += 1
            if rows == 0:
                raise RuntimeError("The CSV contains no data records.")
        metadata = {
            "publisher": "Oracle's Elixir / Tim Sevenhuysen",
            "downloads_page": DOWNLOADS_PAGE,
            "folder_url": FOLDER_URL,
            "file_url": f"https://drive.google.com/file/d/{info['id']}/view",
            "download_url": url,
            "resolved_download_url": resolved_url,
            "filename": filename,
            "season": info["year"],
            "available_seasons": sorted({x["year"] for x in listing.files.values()}),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_last_modified_http": headers.get("Last-Modified"),
            "bytes": temporary.stat().st_size,
            "sha256": digest.hexdigest(),
            "rows": rows,
            "columns": len(columns),
            "scope_note": "Newest season in the publisher's public folder at retrieval; an evolving snapshot, not a guarantee of complete competition coverage.",
        }
        temporary.replace(target)
        manifest = target.with_suffix(".manifest.json")
        manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(metadata, indent=2), flush=True)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, help="Select a specific available season; default: newest.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--schema-only", action="store_true", help="Save only the public CSV header for version comparisons.")
    args = parser.parse_args()
    download(args.year, args.output_dir, args.schema_only)
