"""Tests for the config files, scanner selection logic and the
public/private report separation.

Run them from the project root with:

    py -m unittest discover -s tests -v
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import export_report  # noqa: E402
import scan_places  # noqa: E402
import score_leads  # noqa: E402

# The only home-services types allowed to be SENT to the API — each one
# verified in Table A of the official place-types docs on 2026-07-10.
VERIFIED_HOME_SERVICES_TYPES = {
    "electrician",
    "plumber",
    "painter",
    "roofing_contractor",
    "locksmith",
}
# Service-category expansion: every included type verified in Table A of the
# official place-types docs on 2026-07-11.
VERIFIED_EXPANSION_TYPES = {
    "nightlife": {"bar", "sports_bar", "pub", "bar_and_grill", "night_club", "karaoke"},
    "event_venues": {"event_venue", "banquet_hall", "wedding_venue"},
    "fitness": {"gym", "fitness_center", "yoga_studio", "sports_club", "sports_coaching"},
    "pet_services": {"pet_care", "pet_boarding_service", "veterinary_care"},
    "transport_rental": {"car_rental", "chauffeur_service", "transportation_service"},
    "moving_storage": {"moving_company", "courier_service", "storage"},
}
# Known-invalid Nearby Search filters that must NEVER be in included_types.
FORBIDDEN_INCLUDED_TYPES = {
    "general_contractor",        # Table B: response-only
    "air_conditioning_contractor",  # does not exist at all
    "photographer",              # does not exist
    "photography_studio",        # does not exist
    "caterer",                   # does not exist (catering_service is valid)
    "handyman",                  # does not exist
    # checked against Table A on 2026-07-11 — none of these exist:
    "pilates_studio",
    "martial_arts",
    "boxing_gym",
    "dance_school",
    "personal_trainer",
    "pet_groomer",
    "pet_grooming",
    "dog_trainer",
    "pet_sitter",
    "party_planner",
    "event_planner",
    "airport_shuttle_service",
    "limousine_service",
    "truck_rental",
}

# The original 36-pair SDE matrix plus the 25-pair service-category
# expansion. The expansion budget is a hard cap: adding pairs beyond it must
# consciously update BOTH numbers here and the scan plan.
ORIGINAL_MATRIX_PAIRS = 36
EXPANSION_CATEGORY_LABELS = set(VERIFIED_EXPANSION_TYPES)
EXPANSION_MAX_REQUESTS = 25


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
        self.assertEqual(len(self.pairs), ORIGINAL_MATRIX_PAIRS + EXPANSION_MAX_REQUESTS)
        labels = {(area["label"], cat["label"]) for area, cat in self.pairs}
        self.assertEqual(len(labels), len(self.pairs))  # no duplicates
        # The two approved pilot combinations must be part of the matrix.
        self.assertIn(("sde-ensanche-ozama", "salons"), labels)
        self.assertIn(("sde-charles-de-gaulle", "auto_services"), labels)

    def test_original_matrix_pairs_are_preserved(self):
        """The expansion must not remove or rewrite any of the 36 original pairs."""
        original = [
            (area, cat)
            for area, cat in self.pairs
            if cat["label"] not in EXPANSION_CATEGORY_LABELS
        ]
        self.assertEqual(len(original), ORIGINAL_MATRIX_PAIRS)

    def test_expansion_stays_inside_the_request_budget(self):
        expansion = [
            (area["label"], cat["label"])
            for area, cat in self.pairs
            if cat["label"] in EXPANSION_CATEGORY_LABELS
        ]
        self.assertGreater(len(expansion), 0)
        self.assertLessEqual(len(expansion), EXPANSION_MAX_REQUESTS)
        # Every planned expansion pair sits in an SDE circle, and the split
        # of the transport plan keeps the two offers distinguishable.
        self.assertIn(("sde-avenida-espana", "nightlife"), expansion)
        self.assertIn(("sde-san-isidro", "transport_rental"), expansion)
        self.assertIn(("sde-hainamosa", "moving_storage"), expansion)

    def test_matrix_only_uses_sde_areas(self):
        for area, _cat in self.pairs:
            self.assertTrue(area["label"].startswith("sde-"), area["label"])

    def test_matrix_categories_filter_keeps_only_wanted_pairs(self):
        kept = scan_places.filter_matrix_pairs(
            self.pairs, "nightlife,fitness", self.categories
        )
        self.assertTrue(kept)
        self.assertEqual(
            {cat["label"] for _area, cat in kept}, {"nightlife", "fitness"}
        )

    def test_matrix_categories_filter_rejects_unknown_labels(self):
        with self.assertRaises(SystemExit):
            scan_places.filter_matrix_pairs(self.pairs, "no_such_label", self.categories)

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

    def test_expansion_categories_use_only_verified_types(self):
        for label, verified in VERIFIED_EXPANSION_TYPES.items():
            self.assertIn(label, self.categories)
            included = set(self.categories[label]["included_types"])
            self.assertEqual(included, verified, label)

    def test_original_categories_are_preserved(self):
        for label in ("salons", "phone_repair", "auto_services",
                      "restaurants", "event_services", "home_services"):
            self.assertIn(label, self.categories)

    def test_category_labels_are_unique(self):
        labels = [cat["label"] for cat in common.load_categories()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_retail_types_stay_out_of_service_categories(self):
        """Nightlife/pets must not pull plain retail into the scan."""
        nightlife = self.categories["nightlife"]
        for retail in ("liquor_store", "convenience_store", "supermarket", "grocery_store"):
            self.assertNotIn(retail, nightlife["included_types"])
            self.assertIn(retail, nightlife["excluded_types"])
        pets = self.categories["pet_services"]
        self.assertNotIn("pet_store", pets["included_types"])
        self.assertIn("pet_store", pets["excluded_types"])

    def test_transport_and_moving_offers_stay_distinguishable(self):
        """A mover must classify as moving_storage, a rental as transport_rental."""
        mover = {
            "name": "Mudanzas El Rapido",
            "types": ["moving_company", "establishment"],
            "primary_type": "moving_company",
            "source_categories": ["transport_rental", "moving_storage"],
            "source_category": "transport_rental",
        }
        rental = {
            "name": "SDE Rent a Car",
            "types": ["car_rental", "establishment"],
            "primary_type": "car_rental",
            "source_categories": ["transport_rental", "moving_storage"],
            "source_category": "moving_storage",
        }
        _, mover_label, mover_conf, _ = score_leads.pick_category(mover, self.categories)
        _, rental_label, rental_conf, _ = score_leads.pick_category(rental, self.categories)
        self.assertEqual(mover_label, "moving_storage")
        self.assertEqual(mover_conf, "high")
        self.assertEqual(rental_label, "transport_rental")
        self.assertEqual(rental_conf, "high")

    def test_primary_type_breaks_category_confidence_ties(self):
        """A nail salon that Google also types as a bar stays a salon, even
        when the nightlife scan found it more recently."""
        salon_with_bar_types = {
            "name": "Centro de Belleza Prueba",
            "primary_type": "nail_salon",
            "types": ["nail_salon", "beauty_salon", "bar", "night_club",
                      "establishment"],
            "source_categories": ["salons", "nightlife"],
            "source_category": "nightlife",  # most recent scan
        }
        _, label, conf, _ = score_leads.pick_category(
            salon_with_bar_types, self.categories
        )
        self.assertEqual(label, "salons")
        self.assertEqual(conf, "high")


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


class BatchRunnerTests(unittest.TestCase):
    """The one-click .bat runners must never silently truncate the matrix.

    scan_places.py --max-requests N runs only the FIRST N pairs, so a cap
    below the configured pair count would skip combinations without any
    error. These tests fail as soon as a runner falls out of sync with
    config/scan_matrix.yml.
    """

    def setUp(self):
        areas = common.load_scan_areas()
        categories = common.load_categories()
        self.pairs = common.load_scan_matrix(areas, categories)
        self.expansion_pairs = [
            (area, cat)
            for area, cat in self.pairs
            if cat["label"] in EXPANSION_CATEGORY_LABELS
        ]

    @staticmethod
    def read_caps(bat_name):
        content = (PROJECT_ROOT / bat_name).read_text(encoding="utf-8")
        caps = [int(n) for n in re.findall(r"--max-requests\s+(\d+)", content)]
        checks = [
            int(n)
            for n in re.findall(r"Total requests that would be made: (\d+)", content)
        ]
        return content, caps, checks

    def test_full_runner_covers_every_matrix_pair(self):
        content, caps, checks = self.read_caps("run_scan.bat")
        total = len(self.pairs)
        self.assertTrue(caps, "run_scan.bat has no --max-requests cap")
        for cap in caps:
            self.assertGreaterEqual(
                cap, total,
                f"run_scan.bat caps at {cap} but the matrix has {total} pairs "
                "— the runner would silently skip combinations",
            )
        # The dry-run validation line must expect the real matrix size too.
        for expected in checks:
            self.assertEqual(expected, total)
        self.assertIn("--dry-run", content)

    def test_expansion_runner_matches_the_expansion_pairs(self):
        content, caps, checks = self.read_caps("run_service_expansion_scan.bat")
        total = len(self.expansion_pairs)
        self.assertTrue(caps)
        for cap in caps:
            self.assertGreaterEqual(cap, total)
        for expected in checks:
            self.assertEqual(expected, total)
        # It must scan exactly the expansion categories, no more, no less.
        match = re.search(r"--matrix-categories\s+(\S+)", content)
        self.assertIsNotNone(match)
        listed = set(match.group(1).replace("%CATS%", "").split(",")) - {""}
        if not listed:  # categories live in a CATS variable
            var = re.search(r"set CATS=(\S+)", content)
            self.assertIsNotNone(var)
            listed = set(var.group(1).split(","))
        self.assertEqual(listed, EXPANSION_CATEGORY_LABELS)


class FilterWidgetTests(unittest.TestCase):
    """Both report filters (neighborhood + category) must render and tag rows."""

    LEADS = [
        {"name": "Bar Uno", "matched_category": "nightlife",
         "source_areas": ["sde-avenida-espana"]},
        {"name": "Gimnasio Dos", "matched_category": "fitness",
         "source_areas": ["sde-los-mina"]},
    ]
    AREA_NAMES = {"sde-avenida-espana": "Av. Espana", "sde-los-mina": "Los Mina"}

    def test_filterbar_offers_both_dropdowns(self):
        widget = export_report.build_area_filter(self.LEADS, self.AREA_NAMES)
        self.assertIn('id="area-filter"', widget)
        self.assertIn('id="category-filter"', widget)
        self.assertIn("nightlife", widget)
        self.assertIn("fitness", widget)
        self.assertIn("Av. Espana", widget)

    def test_rows_carry_area_and_category_attributes(self):
        attrs = export_report.data_areas_attr(self.LEADS[0])
        self.assertIn('data-areas="sde-avenida-espana"', attrs)
        self.assertIn('data-cats="nightlife"', attrs)

    def test_filter_script_wires_up_both_selects(self):
        self.assertIn("area-filter", export_report.FILTER_SCRIPT)
        self.assertIn("category-filter", export_report.FILTER_SCRIPT)
        self.assertIn("data-cats", export_report.FILTER_SCRIPT)


class MergeBehaviorTests(unittest.TestCase):
    def test_merge_source_lists_keeps_order_and_dedupes(self):
        merged = scan_places.merge_source_lists(["a", "b"], ["b", "c"])
        self.assertEqual(merged, ["a", "b", "c"])

    def test_dedupe_preserves_every_area_and_category_that_found_a_place(self):
        """A business found by several scans keeps one row with merged history."""
        older = {
            "id": "place-1", "name": "Bar Terraza X", "phone": "809-555-1111",
            "source_area": "sde-avenida-espana", "source_category": "restaurants",
            "source_areas": ["sde-avenida-espana"],
            "source_categories": ["restaurants"],
            "types": ["bar", "restaurant"], "scanned_at": "2026-07-06T00:00:00+00:00",
        }
        newer = {
            "id": "place-1", "name": "Bar Terraza X", "phone": "809-555-2222",
            "source_area": "sde-ensanche-ozama", "source_category": "nightlife",
            "source_areas": ["sde-ensanche-ozama"],
            "source_categories": ["nightlife"],
            "types": ["bar", "restaurant"], "scanned_at": "2026-07-11T00:00:00+00:00",
        }
        merged, duplicates = score_leads.upgrade_and_dedupe([older, newer])
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(merged), 1)
        lead = merged[0]
        self.assertEqual(lead["phone"], "809-555-2222")  # newest scan wins
        self.assertEqual(
            lead["source_areas"], ["sde-avenida-espana", "sde-ensanche-ozama"]
        )
        self.assertEqual(lead["source_categories"], ["restaurants", "nightlife"])


if __name__ == "__main__":
    unittest.main()
