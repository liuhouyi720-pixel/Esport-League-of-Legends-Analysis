"""Stream JSONL archives to inspect observed structure without assuming a LoL schema.

Transaction intervals describe the inspected transaction timestamp field. They
must not be reported as player-position sampling frequency or server tick rate.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import zipfile

SENSITIVE = {"apikey", "xapikey", "authorization", "token", "password", "secret"}


def preview(value, depth=0):
    """Return a clearly truncated, credential-redacted preview, not altered raw data."""
    if depth > 7:
        return "<preview depth limit>"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.sub(r"[^a-z]", "", k.lower()) in SENSITIVE else preview(v, depth+1))
                for k, v in list(value.items())[:35]}
    if isinstance(value, list):
        result = [preview(v, depth+1) for v in value[:3]]
        if len(value) > 3:
            result.append({"_preview_omitted_items": len(value)-3})
        return result
    return value[:250] if isinstance(value, str) and len(value) > 250 else value


def value_type(value):
    if value is None: return "null"
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, float): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, dict): return "object"
    if isinstance(value, list): return "array"
    return type(value).__name__


def walk_fields(value, path="$", depth=0, max_depth=10):
    yield path, value_type(value), value
    if depth >= max_depth:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if re.sub(r"[^a-z]", "", key.lower()) not in SENSITIVE:
                yield from walk_fields(child, path+"."+key, depth+1, max_depth)
    elif isinstance(value, list):
        for child in value:
            yield from walk_fields(child, path+"[]", depth+1, max_depth)


def events_in_record(record):
    """Recognize explicit event arrays; do not equate every JSONL row to one event."""
    if not isinstance(record, dict):
        return []
    if isinstance(record.get("events"), list):
        return [("$.events[]", event) for event in record["events"]]
    transaction = record.get("transaction")
    if isinstance(transaction, dict) and isinstance(transaction.get("events"), list):
        return [("$.transaction.events[]", event) for event in transaction["events"]]
    if "eventType" in record or ("type" in record and any(k in record for k in ["actor", "action", "target"])):
        return [("$", record)]
    return []


def event_type(event):
    if not isinstance(event, dict):
        return "<non-object event item>"
    for name in ["type", "eventType", "eventName"]:
        if isinstance(event.get(name), (str, int)):
            return str(event[name])
    action = event.get("action")
    if isinstance(action, str):
        return "action="+action
    if isinstance(action, dict) and isinstance(action.get("type"), str):
        return "action.type="+action["type"]
    return "<type not identified; inspect raw fields>"


def aware_iso_seconds(value):
    """Parse timezone-aware ISO strings only. Never guess numeric clock units."""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d\d-\d\d[T ]", value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def write_csv(path, rows, columns):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def inspect_archives(paths, output_dir, max_records=100000):
    """Inspect real ZIP/JSONL records, with explicit scan limits and parse errors."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    schema_counts = Counter()
    presence = Counter()
    examples = {}
    event_counts = Counter()
    game_refs = Counter()
    record_counts = Counter()
    clock_values = defaultdict(list)
    errors, samples, members = [], [], []
    fingerprints = set()
    duplicate_records = 0
    inspected = 0
    limited = False
    sources = []
    for path in paths:
        path = Path(path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024), b""):
                digest.update(chunk)
        sources.append({"filename": path.name, "sha256": digest.hexdigest(), "bytes": path.stat().st_size})
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                members.append({"archive": path.name, "member": info.filename, "compressed_bytes": info.compress_size,
                                "uncompressed_bytes": info.file_size, "is_jsonl": info.filename.lower().endswith(".jsonl")})
    for path in paths:
        if limited:
            break
        path = Path(path)
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.lower().endswith(".jsonl"):
                    continue
                with archive.open(info) as stream:
                    for line_number, raw_line in enumerate(stream, start=1):
                        if not raw_line.strip():
                            continue
                        if max_records and inspected >= max_records:
                            limited = True
                            break
                        if len(raw_line) > 16*1024*1024:
                            errors.append({"archive":path.name, "member":info.filename, "line":line_number, "error":"record exceeds 16 MB limit"})
                            continue
                        try:
                            record = json.loads(raw_line)
                        except (ValueError, UnicodeDecodeError):
                            errors.append({"archive":path.name, "member":info.filename, "line":line_number, "error":"invalid JSONL record"})
                            continue
                        inspected += 1
                        scope = (path.name, info.filename)
                        record_counts[scope] += 1
                        fingerprint = (scope, hashlib.sha256(raw_line).digest())
                        duplicate_records += fingerprint in fingerprints
                        fingerprints.add(fingerprint)
                        if len(samples) < 3:
                            samples.append({"archive":path.name, "member":info.filename, "line":line_number, "preview":preview(record)})
                        seen = set()
                        for field, kind, value in walk_fields(record):
                            key = (*scope, field, kind)
                            schema_counts[key] += 1
                            seen.add(key)
                            if kind not in {"array", "object"}:
                                examples.setdefault(key, str(value)[:150])
                                if field.lower().endswith(".gameid") and value is not None:
                                    game_refs[(*scope, field, str(value))] += 1
                        presence.update(seen)
                        for events_path, event in events_in_record(record):
                            event_counts[(*scope, events_path, event_type(event))] += 1
                        if isinstance(record, dict):
                            for field, value in record.items():
                                if re.search(r"time|occurredat", field, flags=re.I):
                                    seconds = aware_iso_seconds(value)
                                    if seconds is not None:
                                        clock_values[(*scope, "$."+field)].append(seconds)
                if limited:
                    break
    schema_rows = [{"archive":key[0], "member":key[1], "path":key[2], "json_type":key[3],
                    "occurrences":count, "records_with_path_and_type":presence[key],
                    "records_inspected_in_member":record_counts[key[:2]],
                    "presence_pct":100*presence[key]/record_counts[key[:2]], "example":examples.get(key, "<container>")}
                   for key,count in schema_counts.items()]
    event_rows = [{"archive":k[0], "member":k[1], "events_path":k[2], "event_type":k[3], "event_items":v} for k,v in event_counts.items()]
    game_rows = [{"archive":k[0], "member":k[1], "path":k[2], "referenced_game_id":k[3], "occurrences":v} for k,v in game_refs.items()]
    time_rows = []
    for key, values in clock_values.items():
        deltas = [b-a for a,b in zip(values, values[1:])]
        positive = [d for d in deltas if d>0]
        time_rows.append({"archive":key[0], "member":key[1], "timestamp_path":key[2],
            "observed_timestamps":len(values), "first_utc":datetime.fromtimestamp(values[0],timezone.utc).isoformat(),
            "last_utc":datetime.fromtimestamp(values[-1],timezone.utc).isoformat(),
            "equal_adjacent_timestamps":sum(d==0 for d in deltas), "backward_steps":sum(d<0 for d in deltas),
            "median_positive_transaction_delta_seconds":statistics.median(positive) if positive else None,
            "max_positive_transaction_delta_seconds":max(positive) if positive else None})
    write_csv(output_dir/"schema_paths.csv", schema_rows, ["archive","member","path","json_type","occurrences","records_with_path_and_type","records_inspected_in_member","presence_pct","example"])
    write_csv(output_dir/"event_type_counts.csv", event_rows, ["archive","member","events_path","event_type","event_items"])
    write_csv(output_dir/"explicit_game_id_references.csv", game_rows, ["archive","member","path","referenced_game_id","occurrences"])
    write_csv(output_dir/"transaction_timestamp_audit.csv", time_rows, ["archive","member","timestamp_path","observed_timestamps","first_utc","last_utc","equal_adjacent_timestamps","backward_steps","median_positive_transaction_delta_seconds","max_positive_transaction_delta_seconds"])
    write_csv(output_dir/"archive_members.csv", members, ["archive","member","compressed_bytes","uncompressed_bytes","is_jsonl"])
    write_csv(output_dir/"parse_errors.csv", errors, ["archive","member","line","error"])
    (output_dir/"record_previews.json").write_text(json.dumps(samples,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    summary = {"source_files":sources, "source_records_inspected":inspected,
               "event_items_identified":sum(event_counts.values()), "distinct_event_type_labels":len({k[3] for k in event_counts}),
               "exact_duplicate_record_lines":duplicate_records, "parse_errors":len(errors),
               "record_limit":max_records, "stopped_at_record_limit":limited,
               "jsonl_members_found":sum(m["is_jsonl"] for m in members),
               "scope_note":"Observed fields only. JSONL rows may be transactions containing multiple events. Transaction intervals are not position-update frequency. Numeric timestamp units are not inferred."}
    (output_dir/"inspection.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    return summary
