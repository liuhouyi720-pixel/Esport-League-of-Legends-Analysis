"""Offline regression tests. Fixtures are synthetic and are not research data."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request
import zipfile

from analysis.grid_client import GridClient, GridError, SafeRedirects, load_api_key, matches_target, read_local_settings, select_event_files
from analysis.grid_events import aware_iso_seconds, inspect_archives, preview

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / ".cache" / "grid_tests"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


class FakeOpener:
    def __init__(self, content):
        self.content = content
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.content).encode())


class GridWorkflowTests(unittest.TestCase):
    def temporary(self):
        folder = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.assertTrue(Path(folder.name).resolve().is_relative_to(TEMP_ROOT.resolve()))
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_local_credentials_are_explicit_and_missing_key_does_not_request_network(self):
        folder = self.temporary()
        env = folder / ".env"
        env.write_text('# local fixture\nGRID_API_KEY="local-test-key"\nUNRELATED_SECRET=do-not-read\nGRID_PLATFORM=open\n')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(read_local_settings(env), {"GRID_API_KEY":"local-test-key", "GRID_PLATFORM":"open"})
            self.assertEqual(load_api_key(env), "local-test-key")
            with self.assertRaises(GridError):
                load_api_key(folder / "absent.env")

    def test_key_is_a_header_and_graphql_errors_are_redacted(self):
        key = "synthetic-secret-for-test"
        opener = FakeOpener({"errors":[{"message":"rejected "+key}]})
        client = GridClient(key, opener=opener, throttle=False)
        with self.assertRaises(GridError) as caught:
            client.graphql("query { titles { id name } }")
        self.assertNotIn(key, str(caught.exception))
        request = opener.requests[0]
        self.assertEqual(request.get_header("X-api-key"), key)
        self.assertNotIn(key, request.full_url)
        self.assertNotIn(key, request.data.decode())

    def test_key_is_not_sent_to_arbitrary_hosts_or_cross_host_redirects(self):
        opener = FakeOpener({})
        client = GridClient("synthetic-key", opener=opener, throttle=False)
        with self.assertRaises(GridError):
            client.get_json("https://example.com/data")
        self.assertFalse(opener.requests)
        original = Request("https://api.grid.gg/file-download/events/grid/series/123", headers={"x-api-key":"synthetic-key"})
        redirected = SafeRedirects().redirect_request(original, None, 302, "", {}, "https://cdn.example.com/signed.zip")
        self.assertIsNone(redirected.get_header("X-api-key"))

    def test_ready_event_selection_avoids_duplicate_riot_representations(self):
        files = {"files":[{"id":"events-grid-compressed","status":"processing"},
                          {"id":"events-riot-compressed","status":"ready"},
                          {"id":"events-riot-game-1","status":"ready"},
                          {"id":"state-grid","status":"ready"}]}
        self.assertEqual([f["id"] for f in select_event_files(files)], ["events-riot-compressed"])
        self.assertEqual(select_event_files(files,"grid"), [])

    def test_team_names_do_not_override_the_game_title(self):
        config = {"team_aliases":[["Gen.G","Gen.G Esports"],["Invictus Gaming","IG"]]}
        node = {"title":{"name":"League of Legends"},"teams":[{"baseInfo":{"name":"IG"}},{"baseInfo":{"name":"Gen.G Esports"}}]}
        self.assertTrue(matches_target(node,config))
        node["title"]["name"] = "VALORANT"
        self.assertFalse(matches_target(node,config))

    def test_download_filename_cannot_escape_output_folder(self):
        client = GridClient("synthetic-key", opener=FakeOpener({}), throttle=False)
        with self.assertRaises(GridError):
            client.download({"status":"ready","fileName":"../escape.zip","fullURL":"https://api.grid.gg/data"},self.temporary())

    def test_zip_download_requests_zip_and_preserves_existing_file_on_invalid_body(self):
        folder = self.temporary()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload,"w") as archive:
            archive.writestr("events.jsonl",'{"events":[]}\n')
        class RawOpener(FakeOpener):
            def open(self,request,timeout):
                self.requests.append(request)
                return io.BytesIO(self.content)
        opener = RawOpener(payload.getvalue())
        client = GridClient("synthetic-key",opener=opener,throttle=False)
        info = {"id":"events-grid","status":"ready","fileName":"events.zip","fullURL":"https://api.grid.gg/file-download/events/grid/series/123"}
        result = client.download(info,folder)
        self.assertEqual(opener.requests[0].get_header("Accept"),"application/zip")
        self.assertTrue(zipfile.is_zipfile(folder/result["filename"]))
        original = (folder/"events.zip").read_bytes()
        opener.content = b'<html>access error</html>'
        with self.assertRaises(GridError):
            client.download(info,folder)
        self.assertEqual((folder/"events.zip").read_bytes(),original)
        self.assertFalse((folder/"events.zip.part").exists())

    def test_transactions_and_nested_event_items_have_different_grains(self):
        folder = self.temporary()
        archive = folder/"synthetic_fixture.zip"
        record = {"occurredAt":"2026-11-07T22:00:00Z","events":[
            {"type":"fixture-event-A","actor":{"id":"p1"},"gameId":"g1"},
            {"type":"fixture-event-B","actor":{"id":"p2"},"gameId":"g1"}]}
        second = {"occurredAt":"2026-11-07T22:00:00.500Z","events":[]}
        with zipfile.ZipFile(archive,"w") as z:
            z.writestr("events.jsonl",json.dumps(record)+"\n"+json.dumps(second)+"\n")
        summary = inspect_archives([archive],folder/"report",max_records=0)
        self.assertEqual(summary["source_records_inspected"],2)
        self.assertEqual(summary["event_items_identified"],2)
        self.assertEqual(summary["distinct_event_type_labels"],2)
        import csv
        with (folder/"report/schema_paths.csv").open() as f:
            actor = next(r for r in csv.DictReader(f) if r["path"]=="$.events[].actor.id")
        self.assertEqual(int(actor["occurrences"]),2)
        self.assertEqual(int(actor["records_with_path_and_type"]),1)
        with (folder/"report/transaction_timestamp_audit.csv").open() as f:
            timing = next(csv.DictReader(f))
        self.assertEqual(float(timing["median_positive_transaction_delta_seconds"]),.5)

    def test_scan_limits_parse_errors_and_duplicate_lines_are_visible(self):
        folder = self.temporary(); archive=folder/"synthetic_fixture.zip"
        line=json.dumps({"events":[],"timestamp":1234567890})+"\n"
        with zipfile.ZipFile(archive,"w") as z:
            z.writestr("events.jsonl",line+"invalid-json\n"+line+line)
        result=inspect_archives([archive],folder/"report",max_records=2)
        self.assertTrue(result["stopped_at_record_limit"])
        self.assertEqual(result["source_records_inspected"],2)
        self.assertEqual(result["parse_errors"],1)
        self.assertEqual(result["exact_duplicate_record_lines"],1)
        self.assertIsNone(aware_iso_seconds(1234567890))
        self.assertIsNone(aware_iso_seconds("2026-11-07T22:00:00"))

    def test_record_previews_redact_credentials_and_preserve_zero(self):
        result=preview({"x-api-key":"synthetic-key","events":[0,1,2,3,4]})
        self.assertEqual(result["x-api-key"],"[REDACTED]")
        self.assertEqual(result["events"][0],0)
        self.assertEqual(result["events"][-1]["_preview_omitted_items"],2)


if __name__ == "__main__":
    unittest.main()
