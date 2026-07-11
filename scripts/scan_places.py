"""Scan for nearby businesses using the official Google Places API.

This script calls Nearby Search (New):
    POST https://places.googleapis.com/v1/places:searchNearby
It does NOT scrape the Google Maps website.

Examples:
    py scripts/scan_places.py --area default --category salons
    py scripts/scan_places.py --area default            (all categories, one area)
    py scripts/scan_places.py --category restaurants    (one category, all areas)
    py scripts/scan_places.py --area-prefix sde         (all SDE areas)
    py scripts/scan_places.py --matrix                  (targeted SDE scan matrix)
    py scripts/scan_places.py --matrix --dry-run        (preview, zero API calls)
    py scripts/scan_places.py --matrix --max-requests 40
    py scripts/scan_places.py --all                     (every area x category)
    py scripts/scan_places.py --all --fresh             (discard old results first)
    py scripts/scan_places.py --list                    (show configured labels)

Results are merged into data/scan_results.json, deduplicated by place id.
Re-running a scan refreshes the stored data for the places it finds.
Every real run is appended to data/request_log.json so you can check how
many API requests were made this month.
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

from common import (
    PROJECT_ROOT,
    REQUEST_LOG_FILE,
    SCAN_RESULTS_FILE,
    ConfigError,
    die,
    load_categories,
    load_json,
    load_scan_areas,
    load_scan_matrix,
    save_json,
    utc_now_iso,
)

NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Ask only for the fields we actually use. The field mask controls both what
# Google returns AND which billing tier (SKU) each request falls into.
# Never use "*" (the wildcard) — it requests every field and bills accordingly.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.primaryType",
        "places.types",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
    ]
)

# Which SKU tier each requested field falls into, per the official SKU
# details page (checked 2026-07-10). The most expensive field in the mask
# decides the SKU for the whole request. Atmosphere fields (reviews,
# editorialSummary, photos, serves*/allows* flags...) are deliberately
# never requested.
NEARBY_PRO_FIELDS = {
    "places.id",
    "places.displayName",
    "places.primaryType",
    "places.types",
    "places.formattedAddress",
    "places.googleMapsUri",
    "places.businessStatus",
}
NEARBY_ENTERPRISE_FIELDS = {
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
}


def billing_sku(field_mask=FIELD_MASK):
    """Name the Nearby Search SKU tier this field mask triggers."""
    fields = set(field_mask.split(","))
    unknown = fields - NEARBY_PRO_FIELDS - NEARBY_ENTERPRISE_FIELDS
    if unknown:
        # A field we never classified — assume the worst tier and say so.
        return (
            "UNKNOWN (possibly Enterprise + Atmosphere) — unclassified field(s): "
            + ", ".join(sorted(unknown))
        )
    if fields & NEARBY_ENTERPRISE_FIELDS:
        return "Nearby Search Enterprise"
    return "Nearby Search Pro"


MAX_RESULTS_PER_REQUEST = 20  # hard limit of Nearby Search (New); no paging
REQUEST_TIMEOUT_SECONDS = 30
PAUSE_BETWEEN_REQUESTS = 0.5  # small pause between API calls, just to be gentle


def get_api_key():
    """Read the API key from .env / environment variables. Never hardcode it."""
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key or api_key == "paste-your-key-here":
        die(
            "GOOGLE_PLACES_API_KEY is not set.\n"
            "  1. Copy .env.example to .env (in the project folder).\n"
            "  2. Paste your Google Places API key into it.\n"
            "  See README.md, section 'Create your .env file'."
        )
    return api_key


def describe_api_error(response):
    """Turn an API error response into a short, human-readable message."""
    try:
        error = response.json().get("error", {})
        detail = error.get("message") or response.text[:200]
        status = error.get("status") or str(response.status_code)
    except ValueError:
        detail = response.text[:200]
        status = str(response.status_code)

    hint = ""
    if response.status_code in (401, 403):
        hint = " (check the API key, and that 'Places API (New)' + billing are enabled)"
    elif response.status_code == 400:
        hint = " (often an invalid place type in categories.yml, or a bad radius)"
    elif response.status_code == 429:
        hint = " (quota exceeded — wait, or check quotas in Google Cloud)"
    return f"API error {status}: {detail}{hint}"


def search_nearby(api_key, area, category):
    """Run one Nearby Search (New) request. Returns a list of raw place dicts."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "includedTypes": category["included_types"],
        "maxResultCount": MAX_RESULTS_PER_REQUEST,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": area["latitude"],
                    "longitude": area["longitude"],
                },
                "radius": float(area["radius_meters"]),
            }
        },
    }

    try:
        response = requests.post(
            NEARBY_SEARCH_URL,
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Covers DNS failures, timeouts, dropped connections, SSL problems...
        raise RuntimeError(f"network problem: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(describe_api_error(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"API returned invalid JSON: {exc}") from exc

    # An empty response body (no "places" key) simply means zero results.
    return payload.get("places", [])


def normalize_place(place, area_label, category_label, scanned_at):
    """Convert one raw API result into our simple internal lead structure."""
    place_id = place.get("id")
    if not place_id:
        return None  # cannot deduplicate without an id — skip it

    display_name = place.get("displayName") or {}
    return {
        "id": place_id,
        "name": display_name.get("text", ""),
        "primary_type": place.get("primaryType", ""),
        "types": place.get("types") or [],
        "address": place.get("formattedAddress", ""),
        "phone": place.get("nationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        "google_maps_url": place.get("googleMapsUri", ""),
        "rating": place.get("rating"),  # stays None when a place has no ratings
        "review_count": place.get("userRatingCount", 0),
        "business_status": place.get("businessStatus", ""),
        # Last scan that saw this place (kept for backward compatibility)...
        "source_area": area_label,
        "source_category": category_label,
        # ...and the full history of areas/categories that found it. When the
        # same place id shows up again, these lists are merged, not replaced.
        "source_areas": [area_label],
        "source_categories": [category_label],
        "scanned_at": scanned_at,
    }


def upgrade_record(lead):
    """Bring a lead saved by an older version up to the current shape."""
    if not isinstance(lead.get("source_areas"), list):
        lead["source_areas"] = [lead["source_area"]] if lead.get("source_area") else []
    if not isinstance(lead.get("source_categories"), list):
        lead["source_categories"] = (
            [lead["source_category"]] if lead.get("source_category") else []
        )
    if not isinstance(lead.get("types"), list):
        lead["types"] = []
    return lead


def merge_source_lists(old_values, new_values):
    """Union of two lists, keeping first-seen order."""
    return list(dict.fromkeys(list(old_values) + list(new_values)))


def pick(items, label, kind):
    """Return all items, or just the one whose label matches."""
    if not label:
        return items
    for item in items:
        if item["label"] == label:
            return [item]
    available = ", ".join(item["label"] for item in items)
    die(f"Unknown {kind} '{label}'. Available: {available}")


def pick_by_prefix(areas, prefix):
    """All areas whose label starts with the prefix. Dies when none match."""
    matched = [area for area in areas if area["label"].startswith(prefix)]
    if not matched:
        available = ", ".join(area["label"] for area in areas)
        die(f"No area label starts with '{prefix}'. Available: {available}")
    return matched


def filter_matrix_pairs(pairs, labels_csv, categories):
    """Keep only the matrix pairs whose category label is in the CSV list."""
    wanted = [label.strip() for label in labels_csv.split(",") if label.strip()]
    if not wanted:
        die("--matrix-categories was given but contains no category labels.")
    known = {category["label"] for category in categories}
    unknown = [label for label in wanted if label not in known]
    if unknown:
        die(
            f"--matrix-categories: unknown categor{'y' if len(unknown) == 1 else 'ies'} "
            f"{', '.join(unknown)}. Available: {', '.join(sorted(known))}"
        )
    kept = [(area, cat) for area, cat in pairs if cat["label"] in wanted]
    if not kept:
        die(
            "--matrix-categories matched no pairs in config/scan_matrix.yml "
            f"for: {', '.join(wanted)}"
        )
    return kept


def describe_area(area):
    """One line describing a scan area (label, name, center, radius)."""
    name = area.get("name", "")
    name_part = f" ({name})" if name and name != area["label"] else ""
    return (
        f"{area['label']}{name_part} — center {area['latitude']}, "
        f"{area['longitude']}, radius {area['radius_meters']} m"
    )


def append_request_log(entry):
    """Append one run summary to data/request_log.json (never crashes a scan)."""
    try:
        log = load_json(REQUEST_LOG_FILE, default=[])
        if not isinstance(log, list):
            log = []
    except ConfigError:
        log = []
    log.append(entry)
    save_json(REQUEST_LOG_FILE, log)


def main():
    parser = argparse.ArgumentParser(
        description="Scan for local businesses via the official Google Places API."
    )
    parser.add_argument("--area", help="label of one scan area from config/scan_areas.yml")
    parser.add_argument(
        "--area-prefix",
        help="scan every area whose label starts with this prefix (e.g. 'sde')",
    )
    parser.add_argument("--category", help="label of one category from config/categories.yml")
    parser.add_argument("--all", action="store_true", help="scan every area x category combination")
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="scan the targeted area x category pairs from config/scan_matrix.yml",
    )
    parser.add_argument(
        "--matrix-categories",
        metavar="LABELS",
        help="with --matrix: only run the matrix pairs whose category is in "
        "this comma-separated list (e.g. 'nightlife,fitness')",
    )
    parser.add_argument("--fresh", action="store_true", help="discard previously scanned data first")
    parser.add_argument("--list", action="store_true", help="list configured areas and categories, then exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the requests that WOULD be made, without calling the API",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        metavar="N",
        help="safety cap: never make more than N API requests in this run",
    )
    args = parser.parse_args()

    try:
        areas = load_scan_areas()
        categories = load_categories()
    except ConfigError as exc:
        die(str(exc))

    if args.list:
        print("Scan areas: " + ", ".join(area["label"] for area in areas))
        print("Categories: " + ", ".join(cat["label"] for cat in categories))
        return

    if args.area and args.area_prefix:
        parser.error("--area and --area-prefix are mutually exclusive")
    if args.matrix and (args.all or args.area or args.area_prefix or args.category):
        parser.error("--matrix cannot be combined with --all/--area/--area-prefix/--category")
    if args.matrix_categories and not args.matrix:
        parser.error("--matrix-categories only makes sense together with --matrix")
    if not args.all and not args.matrix and not args.area and not args.area_prefix and not args.category:
        parser.error(
            "choose what to scan: --all, --matrix, or --area/--area-prefix "
            "and/or --category (see --list)"
        )
    if args.max_requests is not None and args.max_requests < 1:
        parser.error("--max-requests must be at least 1")

    # Build the list of (area, category) requests for this run.
    matched_areas = None
    if args.matrix:
        try:
            pairs = load_scan_matrix(areas, categories)
        except ConfigError as exc:
            die(str(exc))
        if args.matrix_categories:
            pairs = filter_matrix_pairs(pairs, args.matrix_categories, categories)
    else:
        if args.all:
            selected_areas, selected_categories = areas, categories
        else:
            if args.area_prefix:
                selected_areas = pick_by_prefix(areas, args.area_prefix)
                matched_areas = selected_areas
            else:
                selected_areas = pick(areas, args.area, "area")
            selected_categories = pick(categories, args.category, "category")
        pairs = [(area, cat) for area in selected_areas for cat in selected_categories]

    # Cost transparency BEFORE any API call: the exact field mask, the SKU
    # tier it triggers, and exactly how many requests are planned.
    print(f"FieldMask: {FIELD_MASK}")
    print(f"Billing SKU: {billing_sku()} (one request per area x category pair)")
    if matched_areas is not None:
        print(f"--area-prefix '{args.area_prefix}' matched {len(matched_areas)} area(s):")
        for area in matched_areas:
            print(f"  - {describe_area(area)}")
    print(f"Planned requests: {len(pairs)}")

    to_run = pairs
    if args.max_requests is not None and len(pairs) > args.max_requests:
        to_run = pairs[: args.max_requests]
        print(
            f"--max-requests {args.max_requests}: only the first "
            f"{len(to_run)} of {len(pairs)} planned requests will run; "
            f"{len(pairs) - len(to_run)} combination(s) will be skipped."
        )

    if args.dry_run:
        print()
        print("DRY RUN — no API calls will be made. The requests would be:")
        for index, (area, category) in enumerate(to_run):
            print(f"  [{index + 1:>3}] {area['label']} x {category['label']} — {describe_area(area)}")
        skipped = len(pairs) - len(to_run)
        if skipped:
            print(f"  (+{skipped} combination(s) beyond --max-requests, not shown above)")
        print(f"Total requests that would be made: {len(to_run)}")
        return

    api_key = get_api_key()

    # Load previous results so repeated scans merge instead of losing data.
    if args.fresh:
        existing = []
    else:
        try:
            existing = load_json(SCAN_RESULTS_FILE, default=[])
        except ConfigError as exc:
            die(f"{exc}\n  The file may be corrupted — rerun with --fresh to start over.")
    results = {lead["id"]: upgrade_record(lead) for lead in existing if lead.get("id")}

    scanned_at = utc_now_iso()
    new_count = refreshed_count = failures = 0

    for index, (area, category) in enumerate(to_run):
        print(f"[{index + 1}/{len(to_run)}] Scanning '{area['label']}' for '{category['label']}' ...")
        try:
            places = search_nearby(api_key, area, category)
        except RuntimeError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not places:
            print("    No businesses found (try a bigger radius or different place types).")
            continue

        new_here = 0
        for place in places:
            lead = normalize_place(place, area["label"], category["label"], scanned_at)
            if lead is None:
                continue
            previous = results.get(lead["id"])
            if previous is not None:
                # Same place id seen before: the fresh scan wins for the data
                # fields, but we keep every area/category that ever found it.
                lead["source_areas"] = merge_source_lists(
                    previous["source_areas"], lead["source_areas"]
                )
                lead["source_categories"] = merge_source_lists(
                    previous["source_categories"], lead["source_categories"]
                )
                refreshed_count += 1
            else:
                new_count += 1
                new_here += 1
            results[lead["id"]] = lead
        print(f"    {len(places)} places returned ({new_here} new).")

        if index < len(to_run) - 1:
            time.sleep(PAUSE_BETWEEN_REQUESTS)

    save_json(SCAN_RESULTS_FILE, list(results.values()))
    append_request_log(
        {
            "started_at": scanned_at,
            "argv": sys.argv[1:],
            "field_mask": FIELD_MASK,
            "billing_sku": billing_sku(),
            "requests_planned": len(pairs),
            "requests_attempted": len(to_run),
            "requests_failed": failures,
            "new_places": new_count,
            "refreshed_places": refreshed_count,
        }
    )
    skipped = len(pairs) - len(to_run)
    print()
    print(f"Saved {len(results)} unique businesses to {SCAN_RESULTS_FILE}")
    print(f"  API requests made: {len(to_run)} (failed: {failures})"
          + (f" | skipped by --max-requests: {skipped}" if skipped else ""))
    print(f"  new this run: {new_count} | refreshed: {refreshed_count}")
    print(f"  usage log: {REQUEST_LOG_FILE}")
    print("Next step: py scripts/score_leads.py")

    if to_run and failures == len(to_run):
        die("Every request failed — check your API key, billing and network, then retry.")


if __name__ == "__main__":
    main()
