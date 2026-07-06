"""Scan for nearby businesses using the official Google Places API.

This script calls Nearby Search (New):
    POST https://places.googleapis.com/v1/places:searchNearby
It does NOT scrape the Google Maps website.

Examples:
    py scripts/scan_places.py --area default --category salons
    py scripts/scan_places.py --area default            (all categories, one area)
    py scripts/scan_places.py --category restaurants    (one category, all areas)
    py scripts/scan_places.py --all                     (every area x category)
    py scripts/scan_places.py --all --fresh             (discard old results first)
    py scripts/scan_places.py --list                    (show configured labels)

Results are merged into data/scan_results.json, deduplicated by place id.
Re-running a scan refreshes the stored data for the places it finds.
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

from common import (
    PROJECT_ROOT,
    SCAN_RESULTS_FILE,
    ConfigError,
    die,
    load_categories,
    load_json,
    load_scan_areas,
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
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
    ]
)

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
        "address": place.get("formattedAddress", ""),
        "phone": place.get("nationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        "google_maps_url": place.get("googleMapsUri", ""),
        "rating": place.get("rating"),  # stays None when a place has no ratings
        "review_count": place.get("userRatingCount", 0),
        "business_status": place.get("businessStatus", ""),
        "source_area": area_label,
        "source_category": category_label,
        "scanned_at": scanned_at,
    }


def pick(items, label, kind):
    """Return all items, or just the one whose label matches."""
    if not label:
        return items
    for item in items:
        if item["label"] == label:
            return [item]
    available = ", ".join(item["label"] for item in items)
    die(f"Unknown {kind} '{label}'. Available: {available}")


def main():
    parser = argparse.ArgumentParser(
        description="Scan for local businesses via the official Google Places API."
    )
    parser.add_argument("--area", help="label of one scan area from config/scan_areas.yml")
    parser.add_argument("--category", help="label of one category from config/categories.yml")
    parser.add_argument("--all", action="store_true", help="scan every area x category combination")
    parser.add_argument("--fresh", action="store_true", help="discard previously scanned data first")
    parser.add_argument("--list", action="store_true", help="list configured areas and categories, then exit")
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

    if not args.all and not args.area and not args.category:
        parser.error("choose what to scan: --all, or --area and/or --category (see --list)")

    if args.all:
        selected_areas, selected_categories = areas, categories
    else:
        selected_areas = pick(areas, args.area, "area")
        selected_categories = pick(categories, args.category, "category")

    api_key = get_api_key()

    # Load previous results so repeated scans merge instead of losing data.
    if args.fresh:
        existing = []
    else:
        try:
            existing = load_json(SCAN_RESULTS_FILE, default=[])
        except ConfigError as exc:
            die(f"{exc}\n  The file may be corrupted — rerun with --fresh to start over.")
    results = {lead["id"]: lead for lead in existing if lead.get("id")}

    scanned_at = utc_now_iso()
    pairs = [(area, cat) for area in selected_areas for cat in selected_categories]
    new_count = refreshed_count = failures = 0

    for index, (area, category) in enumerate(pairs):
        print(f"[{index + 1}/{len(pairs)}] Scanning '{area['label']}' for '{category['label']}' ...")
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
            if lead["id"] in results:
                refreshed_count += 1
            else:
                new_count += 1
                new_here += 1
            results[lead["id"]] = lead
        print(f"    {len(places)} places returned ({new_here} new).")

        if index < len(pairs) - 1:
            time.sleep(PAUSE_BETWEEN_REQUESTS)

    save_json(SCAN_RESULTS_FILE, list(results.values()))
    print()
    print(f"Saved {len(results)} unique businesses to {SCAN_RESULTS_FILE}")
    print(f"  new this run: {new_count} | refreshed: {refreshed_count} | failed requests: {failures}")
    print("Next step: py scripts/score_leads.py")

    if pairs and failures == len(pairs):
        die("Every request failed — check your API key, billing and network, then retry.")


if __name__ == "__main__":
    main()
