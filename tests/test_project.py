"""Tests for the config files, scanner selection logic and the
public/private report separation.

Run them from the project root with:

    py -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import export_report  # noqa: E402
import scan_places  # noqa: E402

# The only home-services types allowed to be SENT to the API — each one
# verified in Table A of the official place-types docs on 2026-07-10.
VERIFIED_HOME_SERVICES_TYPES = {
    "electrician",
    "plumber",
    "painter",
    "roofing_contractor",
    "locksmith",
}
# Known-invalid Nearby Search filters that must NEVER be in included_types.
FORBIDDEN_INCLUDED_TYPES = {
    "general_contractor",        # Table B: response-only
    "air_conditioning_contractor",  # does not exist at all
    "photographer",              # does not exist
    "photography_studio",        # does not exist
    "caterer",                   # does not exist (catering_service is valid)
    "handyman",                  # does not exist
}


class ScanAreasTests(unittest.TestCase):
    def setUp(self):
        self.areas = common.load_scan_areas()

    def test_sde_areas_exist_with_names_and_sane_radii(self):
        sde = [area for area in self.areas if area["label"].startswith("sde-")]
        self.assertEqual(len(sde), 12)
        for area in sde:
            self.assertTrue(area["name"].strip(), area["label"])
            self.assertGreaterEqual(area["radius_meters"], 1500, area["label"])
            self.assertLessEqual(area["radius_meters"], 2500, area["label"])
            # Everything must actually be in the Santo Domingo Este box.
            self.assertTrue(18.45 <= area["latitude"] <= 18.56, area["label"])
            self.assertTrue(-69.90 <= area["longitude"] <= -69.74, area["label"])

    def test_legacy_areas_still_present(self):
        labels = {area["label"] for area in self.areas}
        self.assertIn("default", labels)
        self.assertIn("east-side", labels)


class ScanMatrixTests(unittest.TestCase):
    def setUp(self):
        self.areas = common.load_scan_areas()
        self.categories = common.load_categories()
        self.pairs = common.load_scan_matrix(self.areas, self.categories)

    def test_matrix_loads_expected_pairs(self):
        self.assertEqual(len(self.pairs), 36)
        labels = {(area["label"], cat["label"]) for area, cat in self.pairs}
        self.assertEqual(len(labels), 36)  # no duplicates
        # The two approved pilot combinations must be part of the matrix.
        self.assertIn(("sde-ensanche-ozama", "salons"), labels)
        self.assertIn(("sde-charles-de-gaulle", "auto_services"), labels)

    def test_matrix_only_uses_sde_areas(self):
        for area, _cat in self.pairs:
            self.assertTrue(area["label"].startswith("sde-"), area["label"])

    def test_unknown_area_is_rejected(self):
        bad_areas = [a for a in self.areas if a["label"] == "default"]
        with self.assertRaises(common.ConfigError):
            common.load_scan_matrix(bad_areas, self.categories)


class CategoriesTests(unittest.TestCase):
    def setUp(self):
        self.categories = {cat["label"]: cat for cat in common.load_categories()}

    def test_home_services_uses_only_verified_types(self):
        self.assertIn("home_services", self.categories)
        included = set(self.categories["home_services"]["included_types"])
        self.assertEqual(included, VERIFIED_HOME_SERVICES_TYPES)

    def test_no_category_sends_forbidden_types_to_the_api(self):
        for label, category in self.categories.items():
            bad = set(category["included_types"]) & FORBIDDEN_INCLUDED_TYPES
            self.assertFalse(bad, f"{label} would send invalid types: {bad}")


class AreaPrefixTests(unittest.TestCase):
    AREAS = [
        {"label": "default"},
        {"label": "sde-alma-rosa"},
        {"label": "sde-los-mina"},
    ]

    def test_prefix_matches_only_prefixed_areas(self):
        matched = scan_places.pick_by_prefix(self.AREAS, "sde")
        self.assertEqual([a["label"] for a in matched], ["sde-alma-rosa", "sde-los-mina"])

    def test_no_match_dies_with_clear_error(self):
        with self.assertRaises(SystemExit):
            scan_places.pick_by_prefix(self.AREAS, "zzz")


class BillingTests(unittest.TestCase):
    def test_field_mask_has_no_wildcard_or_atmosphere_fields(self):
        self.assertNotIn("*", scan_places.FIELD_MASK)
        for forbidden in ("reviews", "photos", "editorialSummary", "generativeSummary"):
            self.assertNotIn(forbidden, scan_places.FIELD_MASK)

    def test_current_mask_is_enterprise_tier(self):
        self.assertEqual(scan_places.billing_sku(), "Nearby Search Enterprise")

    def test_unclassified_field_is_flagged_not_hidden(self):
        sku = scan_places.billing_sku(scan_places.FIELD_MASK + ",places.reviews")
        self.assertIn("UNKNOWN", sku)


class PublicPrivateSeparationTests(unittest.TestCase):
    """The public directory must never leak private sales research."""

    SENTINELS = {
        "lead_score": 987654,
        "lead_type": "SENTINEL_LEAD_TYPE",
        "manual_verification_priority": "sentinel-verify-priority",
        "sales_priority": "sentinel-sales-priority",
        "recommended_offer": "SENTINEL OFFER pitch text",
        "notes": "SENTINEL NOTE evidence text",
        "online_presence_status": "sentinel_presence",
        "website_status": "sentinel_website_status",
        "brand_key": "sentinel brand key",
    }

    def make_lead(self):
        lead = {
            "id": "test-place-id",
            "name": "Salon Prueba",
            "address": "Calle 1, Santo Domingo Este",
            "phone": "809-555-0000",
            "website": "https://example.com",
            "google_maps_url": "https://maps.google.com/?cid=1",
            "rating": 4.5,
            "review_count": 12,
            "business_status": "OPERATIONAL",
            "matched_category": "salons",
            "source_category": "salons",
            "source_areas": ["sde-alma-rosa"],
        }
        lead.update(self.SENTINELS)
        return lead

    def render_public_pages(self):
        return export_report.build_public_row(
            self.make_lead(), {"sde-alma-rosa": "Alma Rosa I & II"}
        ) + export_report.build_public_card(
            self.make_lead(), {"sde-alma-rosa": "Alma Rosa I & II"}
        )

    def test_public_pages_contain_only_public_facts(self):
        rendered = self.render_public_pages()
        self.assertIn("Salon Prueba", rendered)
        self.assertIn("809-555-0000", rendered)
        self.assertIn("Alma Rosa", rendered)

    def test_public_pages_leak_no_private_fields(self):
        rendered = self.render_public_pages()
        for field, sentinel in self.SENTINELS.items():
            self.assertNotIn(str(sentinel), rendered, f"public page leaked '{field}'")

    def test_private_row_still_has_the_research_fields(self):
        rendered = export_report.build_html_row(
            self.make_lead(), {"sde-alma-rosa": "Alma Rosa I & II"}
        )
        self.assertIn("SENTINEL OFFER pitch text", rendered)
        self.assertIn("sentinel-sales-priority", rendered)


class MergeBehaviorTests(unittest.TestCase):
    def test_merge_source_lists_keeps_order_and_dedupes(self):
        merged = scan_places.merge_source_lists(["a", "b"], ["b", "c"])
        self.assertEqual(merged, ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
