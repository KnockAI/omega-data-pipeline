import tempfile
import unittest
from pathlib import Path

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

    def test_object_id_ceiling_is_enforced(self):
        client = FakeClient()
        with self.assertRaises(ValueError): client.records(list(range(MAX_OBJECT_IDS + 1)))


if __name__ == "__main__":
    unittest.main()
