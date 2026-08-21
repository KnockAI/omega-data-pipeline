import unittest
from engine.acquisition.rights_vector import CLASSIFICATIONS, create_vector

class RightsVectorTests(unittest.TestCase):
    def test_dataset_scoped_vector_and_hash(self):
        v=create_vector(jurisdiction="CA",agency="county",dataset="parcels",acquisition_method="electronic_copy",statutory_basis="Sierra Club",terms_version="2026",intended_use="internal_computation",evidence_hash="e",rights={"access":"ALLOWED","commercial_internal_use":"ALLOWED","derivative_use":"ALLOWED","redistribution":"DENIED","resale":"DENIED","mailing_use":"DENIED"})
        self.assertTrue(v.allows("commercial_internal_use")); self.assertFalse(v.allows("resale")); self.assertEqual(len(v.hash()),64)
    def test_public_record_does_not_imply_rights(self):
        v=create_vector(jurisdiction="NC",agency="county",dataset="gis",acquisition_method="electronic_copy",statutory_basis="G.S.132-10",terms_version="2026",intended_use="commercial",rights={"access":"ALLOWED","commercial_internal_use":"DENIED"},evidence_hash="e")
        self.assertFalse(v.allows("commercial_internal_use")); self.assertEqual(CLASSIFICATIONS["NC"],"COMMERCIAL_REUSE_CONDITIONAL")
    def test_corrected_classifications(self):
        self.assertEqual(CLASSIFICATIONS["IN"],"ELECTRONIC_MAP_SPECIAL_REGIME"); self.assertEqual(CLASSIFICATIONS["OR"],"INTERGOVERNMENTAL_GIS_RESTRICTED_CLASS"); self.assertEqual(CLASSIFICATIONS["NH"],"DENIED_FOR_GENERAL_COMMERCIAL_INGEST")

if __name__ == '__main__': unittest.main()
