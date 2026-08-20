import json
import tempfile
import unittest
from pathlib import Path

from engine.acquisition.nad_r23 import NADR23Ingest, rights_artifact


class NADR23Test(unittest.TestCase):
    def test_fixture_ingest_and_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / "nad.txt"
            source.write_text("ADDRESS|STATE|COUNTY|LATITUDE|LONGITUDE|UNIT|ID\n10 Main St|IN|Marion|39.7|-86.1|A|1\n10 Main St|IN|Marion|39.7|-86.1|A|1\n20 Oak Rd|UT|Utah|40.7|-111.8|||2\n", encoding="utf-8")
            result = NADR23Ingest(root / "store").ingest(source)
            self.assertEqual(result["counts"]["canonical_records"], 2)
            self.assertEqual(result["counts"]["duplicates"], 1)
            self.assertEqual(result["records_by_state"], {"IN": 1, "UT": 1})
            self.assertTrue((root / "store" / "active.json").exists())
            again = NADR23Ingest(root / "store").ingest(source)
            self.assertEqual(result["canonical_sha256"], again["canonical_sha256"])
            with self.assertRaises(PermissionError): NADR23Ingest.export_mailing_list([])

    def test_rights_artifact_is_non_mailing(self):
        artifact = rights_artifact()
        self.assertEqual(artifact["rights"], "CLEAR_FOR_NON_MAILING_USE")
        self.assertEqual(artifact["constraint"], "NAD_MAILING_LIST_EXPORT=DENIED")


if __name__ == "__main__": unittest.main()
