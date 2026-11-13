"""Small GRID client using local credentials and documented HTTP APIs.

The key is sent only in the x-api-key header to explicitly allowed API hosts.
An Open Access metadata key does not imply permission to download event files.
"""

from __future__ import annotations

from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

API_HOSTS = {"api.grid.gg", "api-op.grid.gg"}


class GridError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def read_local_settings(env_file):
    """Read only the relevant .env assignments; do not execute shell content."""
    settings = {}
    path = Path(env_file)
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"GRID_API_KEY", "GRID_PLATFORM"}:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                settings[key.strip()] = value
    for key in ["GRID_API_KEY", "GRID_PLATFORM"]:
        if os.environ.get(key):
            settings[key] = os.environ[key]
    return settings


def load_api_key(env_file, prompt=False):
    key = read_local_settings(env_file).get("GRID_API_KEY", "").strip()
    if not key and prompt:
        key = getpass.getpass("GRID API key (hidden input): ").strip()
    if not key:
        raise GridError("No GRID_API_KEY is configured. Set it in the project .env file or environment, or use --prompt-key.")
    if "\n" in key or "\r" in key:
        raise GridError("The configured API key contains an unexpected newline.")
    return key


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise GridError("Refusing a non-HTTPS download redirect.")
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None and urlsplit(newurl).hostname not in API_HOSTS:
            redirected.remove_header("X-api-key")
            redirected.remove_header("x-api-key")
        return redirected


class GridClient:
    def __init__(self, key, platform="open", opener=None, throttle=True):
        if platform not in {"open", "standard"}:
            raise GridError("GRID_PLATFORM must be open or standard; use the platform assigned in your portal.")
        self._key = key
        self.base = "https://api-op.grid.gg" if platform == "open" else "https://api.grid.gg"
        self.central_url = self.base + "/central-data/graphql"
        self.state_url = self.base + "/live-data-feed/series-state/graphql"
        self.file_base = "https://api.grid.gg/file-download"
        self.opener = opener or build_opener(SafeRedirects())
        self.throttle = throttle
        self._last_central_call = 0.0

    def _redact(self, value):
        return str(value).replace(self._key, "[REDACTED]") if self._key else str(value)

    def _open(self, url, payload=None, accept="application/json"):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in API_HOSTS or parsed.username or parsed.password:
            raise GridError("Only documented HTTPS GRID API hosts are accepted.")
        if url == self.central_url and self.throttle:
            wait = 3.2-(time.monotonic()-self._last_central_call)
            if wait > 0:
                time.sleep(wait)
            self._last_central_call = time.monotonic()
        headers = {"x-api-key": self._key, "Accept": accept, "User-Agent": "LoLEventStructureAudit/1.0"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            return self.opener.open(Request(url, data=body, headers=headers), timeout=60)
        except HTTPError as error:
            meanings = {401: "Authentication failed or the key belongs to a different platform.",
                        403: "This key is not authorized for this product or series.",
                        404: "The requested series/file is not available at this endpoint.",
                        429: "Rate limited; retry later using the account's documented limits."}
            raise GridError(f"HTTP {error.code}: {meanings.get(error.code, 'GRID request failed.')} Endpoint: {parsed.path}", error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise GridError(self._redact(f"GRID transport failed: {type(error).__name__}. Check connectivity and the configured platform.")) from None

    def get_json(self, url):
        with self._open(url) as response:
            content = response.read(20*1024*1024+1)
        if len(content) > 20*1024*1024:
            raise GridError("The metadata response exceeded the 20 MB inspection limit.")
        try:
            return json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise GridError("GRID returned a non-JSON metadata response.") from None

    def graphql(self, query, state=False):
        with self._open(self.state_url if state else self.central_url, {"query": query}) as response:
            content = response.read(20*1024*1024+1)
        if len(content) > 20*1024*1024:
            raise GridError("The GraphQL response exceeded the metadata inspection limit.")
        try:
            result = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise GridError("GRID returned a non-JSON GraphQL response.") from None
        if result.get("errors"):
            messages = "; ".join(str(e.get("message", "GraphQL error")) for e in result["errors"])
            raise GridError(self._redact(messages[:1500]))
        if not isinstance(result.get("data"), dict):
            raise GridError("No GraphQL data object was returned.")
        return result["data"]

    def find_series(self, config, max_pages=10):
        """Date-bounded paginated discovery. Team names alone are not IDs."""
        after = None
        all_nodes = []
        for _ in range(max_pages):
            cursor = f", after: {json.dumps(after)}" if after else ""
            window = config["date_window_utc"]
            query = """query ExampleSeries {
              allSeries(first: 50%s, orderBy: StartTimeScheduled,
                filter: {startTimeScheduled: {gte: %s, lte: %s}}) {
                edges {node {id startTimeScheduled title {id name}
                  tournament {id name} teams {baseInfo {id name}}}}
                pageInfo {endCursor hasNextPage}
              }
            }""" % (cursor, json.dumps(window["gte"]), json.dumps(window["lte"]))
            connection = self.graphql(query)["allSeries"]
            all_nodes.extend(edge["node"] for edge in connection.get("edges", []))
            page = connection.get("pageInfo", {})
            if not page.get("hasNextPage"):
                break
            next_cursor = page.get("endCursor")
            if not next_cursor or next_cursor == after:
                raise GridError("Pagination did not advance; refusing an incomplete/looping search.")
            after = next_cursor
        else:
            raise GridError("Discovery page limit reached. Narrow the date window instead of guessing a match.")
        matches = [node for node in all_nodes if matches_target(node, config)]
        return matches, all_nodes

    def series(self, series_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(series_id)):
            raise GridError("Invalid GRID series ID format.")
        query = """query SelectedSeries {series(id: %s) {
          id startTimeScheduled title {id name} tournament {id name}
          teams {baseInfo {id name}}
        }}""" % json.dumps(str(series_id))
        result = self.graphql(query).get("series")
        if not result:
            raise GridError("The selected series is not visible to this key.")
        return result

    def series_state(self, series_id):
        query = """query SelectedSeriesState {seriesState(id: %s) {
          id started finished teams {id name won}
          games {teams {id players {id name kills deaths}}}
        }}""" % json.dumps(str(series_id))
        return self.graphql(query, state=True).get("seriesState")

    def list_files(self, series_id):
        return self.get_json(self.file_base + "/list/" + str(series_id))

    def download(self, info, output_dir):
        """Stream a listed ready file and validate its format before replacement."""
        if info.get("status") != "ready":
            raise GridError("The selected file is not ready.")
        filename = str(info.get("fileName", ""))
        if not filename or filename != Path(filename).name or re.search(r"[\\/:\x00]", filename) or filename in {".", ".."}:
            raise GridError("Unsafe or missing filename in the file listing.")
        target = Path(output_dir) / filename
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        try:
            accept = "application/zip" if filename.lower().endswith(".zip") else "application/json"
            with self._open(info["fullURL"], accept=accept) as response, temporary.open("wb") as handle:
                for chunk in iter(lambda: response.read(1024*1024), b""):
                    handle.write(chunk)
                    digest.update(chunk)
            if filename.lower().endswith(".zip"):
                if not zipfile.is_zipfile(temporary):
                    raise GridError("The downloaded file is not a ZIP archive; existing file was preserved.")
                with zipfile.ZipFile(temporary) as archive:
                    if not any(n.lower().endswith((".jsonl", ".json")) for n in archive.namelist()):
                        raise GridError("The archive has no JSON/JSONL data members.")
            elif filename.lower().endswith(".json"):
                with temporary.open(encoding="utf-8-sig") as handle:
                    json.load(handle)
            else:
                raise GridError("Unsupported download format; inspect the file listing.")
            size = temporary.stat().st_size
            temporary.replace(target)
            return {"id": info.get("id"), "filename": filename, "bytes": size,
                    "sha256": digest.hexdigest(), "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
        finally:
            if temporary.exists():
                temporary.unlink()


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def matches_target(node, config):
    title = normalized_name((node.get("title") or {}).get("name", ""))
    if title not in {"leagueoflegends", "lol"}:
        return False
    names = [normalized_name((t.get("baseInfo") or {}).get("name", "")) for t in node.get("teams", [])]
    groups = [{normalized_name(n) for n in group} for group in config["team_aliases"]]
    return len(names) == 2 and any(names[0] in groups[i] and names[1] in groups[1-i] for i in [0, 1])


def select_event_files(listing, kind="auto"):
    ready = [f for f in listing.get("files", []) if f.get("status") == "ready"]
    grid = [f for f in ready if str(f.get("id", "")).startswith("events-grid")]
    riot = [f for f in ready if str(f.get("id", "")).startswith("events-riot")]
    aggregate_riot = [f for f in riot if f.get("id") in {"events-riot", "events-riot-compressed"}]
    riot = aggregate_riot or riot
    if kind == "grid":
        return grid
    if kind == "riot":
        return riot
    if kind == "both":
        return grid+riot
    return grid or riot
