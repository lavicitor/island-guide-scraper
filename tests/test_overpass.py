import json
import pathlib
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scrapers.overpass import (
    _classify_element,
    _derive_car_free,
    _parse_elements,
    scrape_overpass,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.elements = json.loads((FIXTURES / "silba_osm.json").read_text())["elements"]

    def test_beaches_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["beaches"]), 3)

    def test_named_beach_extracted(self):
        pois = _parse_elements(self.elements)
        names = [b["name"] for b in pois["beaches"]]
        self.assertIn("Žalić", names)

    def test_restaurants_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["restaurants"]), 2)

    def test_atm_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["atms"]), 1)

    def test_medical_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["medical"]), 1)

    def test_accommodation_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["accommodation"]), 2)

    def test_landmark_parsed(self):
        pois = _parse_elements(self.elements)
        self.assertEqual(len(pois["landmarks"]), 2)  # tower + ferry terminal

    def test_coordinates_extracted(self):
        pois = _parse_elements(self.elements)
        beach = next(b for b in pois["beaches"] if b["name"] == "Žalić")
        self.assertIsNotNone(beach["coordinates"])
        self.assertAlmostEqual(beach["coordinates"]["lat"], 44.3720, places=3)


class TestCarFree(unittest.TestCase):
    def setUp(self):
        self.elements = json.loads((FIXTURES / "silba_osm.json").read_text())["elements"]

    def test_silba_is_car_free(self):
        # fixture only has footway/path, no motorised highway tags
        self.assertTrue(_derive_car_free(self.elements))

    def test_car_road_makes_not_car_free(self):
        elements_with_road = self.elements + [{
            "type": "way",
            "id": 99999,
            "tags": {"highway": "residential"},
        }]
        self.assertFalse(_derive_car_free(elements_with_road))


class TestClassify(unittest.TestCase):
    def test_natural_beach(self):
        self.assertEqual(_classify_element({"natural": "beach"}), "beach")

    def test_leisure_beach(self):
        self.assertEqual(_classify_element({"leisure": "beach"}), "beach")

    def test_restaurant(self):
        self.assertEqual(_classify_element({"amenity": "restaurant"}), "restaurant")

    def test_cafe(self):
        self.assertEqual(_classify_element({"amenity": "cafe"}), "restaurant")

    def test_atm(self):
        self.assertEqual(_classify_element({"amenity": "atm"}), "atm")

    def test_bank_with_atm(self):
        self.assertEqual(_classify_element({"amenity": "bank", "atm": "yes"}), "atm")

    def test_pharmacy(self):
        self.assertEqual(_classify_element({"amenity": "pharmacy"}), "medical")

    def test_hotel(self):
        self.assertEqual(_classify_element({"tourism": "hotel"}), "accommodation")

    def test_historic(self):
        self.assertEqual(_classify_element({"historic": "tower"}), "landmark")

    def test_unknown_returns_none(self):
        self.assertIsNone(_classify_element({"natural": "tree"}))


class TestScraperResultOnFailure(unittest.TestCase):
    def test_returns_failed_result_on_exception(self):
        client = MagicMock()
        client.post_raw.side_effect = Exception("network error")
        result = scrape_overpass("Silba", 44.37, 14.69, client)
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertIn("network error", result.error)

    def test_falls_back_to_bbox_on_empty_area(self):
        client = MagicMock()
        fixture = json.loads((FIXTURES / "silba_osm.json").read_text())
        # First call (area query) returns empty, second call (bbox) returns fixture
        client.post_raw.side_effect = [
            {"elements": []},
            fixture,
        ]
        result = scrape_overpass("Silba", 44.37, 14.69, client)
        self.assertTrue(result.success)
        self.assertGreater(len(result.data["beaches"]), 0)


if __name__ == "__main__":
    unittest.main()
