import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine.acquisition.nad_transport import NADArcGISClient, NADTransportError


class Response:
    def __init__(self, value): self.value = json.dumps(value).encode()
    def read(self): return self.value


class FakeOfficialService:
    def __init__(self):
        self.version = 1
        self.calls = []

    def __call__(self, url, timeout=0):
        self.calls.append(url)
        path = urlparse(url).path
        query = parse_qs(urlparse(url).query)
        if path.endswith("FeatureServer"):
            return Response({"currentVersion": 12.0, "capabilities": "Query,Create Replica", "version": self.version})
        if path.endswith("/0"):
            return Response({"name": "NAD", "capabilities": "Query,Create Replica", "supportsCreateReplica": True,
                             "supportedQueryFormats": "JSON,geoJSON,PBF", "maxRecordCount": 2000,
                             "fields": [{"name": "ID"}, {"name": "ADDRESS"}, {"name": "STATE"}],
                             "version": self.version})
        if path.endswith("/query") and query.get("returnIdsOnly") == ["true"]:
            return Response({"objectIds": [1, 2]})
        if path.endswith("/query"):
            ids = [int(v) for v in query["objectIds"][0].split(",")]
            return Response({"features": [{"attributes": {"ID": i, "ADDRESS": f"{i} Main St", "STATE": "IN"},
                                             "geometry": {"x": -86.1, "y": 39.7}} for i in ids]})
        raise AssertionError(url)


class NADTransportTest(unittest.TestCase):
    def test_probe_detects_query_replica_and_limit(self):
        fake = FakeOfficialService()
        client = NADArcGISClient(opener=fake)
        result = client.probe()
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["can_create_replica"])
        self.assertEqual(result["max_record_count"], 2000)

    def test_official_partition_acquisition_is_csv_compatible(self):
        fake = FakeOfficialService()
        client = NADArcGISClient(opener=fake)
        with tempfile.TemporaryDirectory() as temp:
            result = client.acquire_csv(Path(temp) / "in.csv", "STATE='IN'")
            self.assertEqual(result["records"], 2)
            text = (Path(temp) / "in.csv").read_text()
            self.assertIn("ADDRESS", text)
            self.assertIn("LATITUDE", text)

    def test_drift_discards_partition_and_fails_closed(self):
        fake = FakeOfficialService()

        def drifting(url, timeout=0):
            response = fake(url, timeout)
            # Change after IDs are read, before the final metadata probe.
            if urlparse(url).path.endswith("/query") and "returnIdsOnly" not in url:
                fake.version = 2
            return response

        client = NADArcGISClient(opener=drifting)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "drift.csv"
            with self.assertRaises(NADTransportError): client.acquire_csv(output)
            self.assertFalse(output.exists())

    def test_invalid_host_is_rejected(self):
        with self.assertRaises(ValueError): NADArcGISClient("https://example.invalid/FeatureServer")


if __name__ == "__main__": unittest.main()
