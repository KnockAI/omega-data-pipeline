import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from engine.acquisition.nad_feature_server import FeatureServerClient, NADFeatureServerIngest, MAX_OBJECT_IDS


class FakeClient:
    layer_url = "https://official.example/nad/FeatureServer/0"

    def __init__(self):
        self.calls = []
        self.fail = True

    def metadata(self):
        return {"editingInfo": {"lastEditDate": 123}, "fields": [
            {"name": "State"}, {"name": "County"}, {"name": "UUID"},
            {"name": "Address"}, {"name": "Latitude"}, {"name": "Longitude"},
        ]}

    def ids(self, where):
        self.calls.append(("ids", where))
        return [1, 2]

    def records(self, ids):
        if len(ids) > MAX_OBJECT_IDS:
            raise ValueError("ArcGIS objectIds request exceeds 2000-record ceiling")
        self.calls.append(("records", list(ids)))
        return [{"UUID": str(i), "Address": f"{i} Main Street", "State": "UT", "County": "Salt Lake",
                 "Latitude": 40.7 + i / 1000, "Longitude": -111.9} for i in ids]


class NADFeatureServerTest(unittest.TestCase):
    def test_state_partition_checkpoint_and_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            result = NADFeatureServerIngest(Path(directory), client).ingest(["UT"], generation="fixture")
            self.assertEqual(result["counts"]["canonical_records"], 2)
            self.assertEqual(result["records_by_state"], {"UT": 2})
            checkpoint = Path(directory) / "fixture.checkpoint.json"
            self.assertTrue(checkpoint.exists())
            again = NADFeatureServerIngest(Path(directory), client).ingest(["UT"], generation="fixture")
            self.assertEqual(result["fingerprint_sha256"], again["fingerprint_sha256"])
            self.assertEqual(len([c for c in client.calls if c[0] == "ids"]), 1)

    def test_feature_server_retries_transient_http_errors(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"objectIds":[1]}'

        attempts = []
        def opener(url, timeout=60):
            attempts.append(url)
            if len(attempts) < 3:
                from urllib.error import HTTPError
                raise HTTPError(url, 503, "retry", {}, None)
            return Response()

        client = FeatureServerClient("https://official.example", opener=opener, sleep=lambda _: None)
        self.assertEqual(client.ids("1=1"), [1])
        self.assertEqual(len(attempts), 3)

    def test_arcgis_payload_429_honors_sixty_second_quota_window(self):
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.payload).encode()

        attempts = []
        def opener(url, timeout=60):
            attempts.append(url)
            if len(attempts) == 1:
                return Response({"error": {"code": 429, "message": "quota"}})
            return Response({"objectIds": [1]})

        sleeps = []
        client = FeatureServerClient("https://official.example", opener=opener, sleep=sleeps.append)
        self.assertEqual(client.ids("1=1"), [1])
        self.assertEqual(sleeps, [60])

    def test_operations_use_explicit_layer_and_query_endpoints(self):
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.payload).encode()

        calls = []
        def opener(url, timeout=60):
            calls.append(url)
            path = urlparse(url).path
            if path.endswith("/query"):
                if "returnIdsOnly" in url: return Response({"objectIds": [1]})
                return Response({"features": [{"attributes": {"OBJECTID": 1, "Address": "1 Main", "State": "UT"}}]})
            return Response({"fields": [], "editingInfo": {"lastEditDate": 1}})

        client = FeatureServerClient("https://official.example/FeatureServer/0", opener=opener, sleep=lambda _: None)
        client.metadata(); client.ids("1=1"); client.records([1]); client.query_page(where="1=1", last_object_id=0)
        self.assertTrue(urlparse(calls[0]).path.endswith("/FeatureServer/0"))
        self.assertTrue(all(urlparse(url).path.endswith("/FeatureServer/0/query") for url in calls[1:]))

    def test_object_id_ceiling_is_enforced(self):
        client = FakeClient()
        with self.assertRaises(ValueError): client.records(list(range(MAX_OBJECT_IDS + 1)))

    def test_keyset_pages_advance_strictly_and_checkpoint(self):
        class KeysetClient(FakeClient):
            def metadata(self): return {"editingInfo": {"lastEditDate": 1}, "fields": []}
            def query_page(self, *, where, last_object_id, upper_object_id=None, out_fields="*"):
                values = [1, 2] if last_object_id == 0 else ([3] if last_object_id == 2 else [])
                return [{"OBJECTID": i, "UUID": str(i), "Address": f"{i} Main", "State": "UT", "County": "Salt Lake", "Latitude": 40.0, "Longitude": -111.0} for i in values]
        with tempfile.TemporaryDirectory() as directory:
            result = NADFeatureServerIngest(Path(directory), KeysetClient()).ingest_oid_range(0, 3, generation="oid")
            self.assertEqual(result["counts"]["source_records"], 3)
            self.assertEqual(result["last_object_id"], 3)


if __name__ == "__main__":
    unittest.main()
