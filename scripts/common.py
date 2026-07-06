"""Shared helpers for the local-business-leads scripts.

All three scripts (scan_places, score_leads, export_report) import from this
module so file paths, config loading and JSON handling live in one place.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Project paths. Everything is resolved relative to this file, so the scripts
# work no matter which folder you run them from.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"

SCAN_AREAS_FILE = CONFIG_DIR / "scan_areas.yml"
CATEGORIES_FILE = CONFIG_DIR / "categories.yml"

# Working files produced by the pipeline. This is short-term research data,
# not a permanent database — refresh it before every outreach round.
SCAN_RESULTS_FILE = DATA_DIR / "scan_results.json"
SCORED_LEADS_FILE = DATA_DIR / "leads_scored.json"

# Google Places API limit: search radius must be 0 < radius <= 50000 meters.
MAX_RADIUS_METERS = 50000

# Sentinel so load_json can tell "no default given" apart from "default=None".
_MISSING = object()


class ConfigError(Exception):
    """Raised when a config or data file is missing or malformed."""


def die(message):
    """Print an error message and stop the script with a failure exit code."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def utc_now_iso():
    """Current time in UTC, e.g. '2026-07-05T15:04:05+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_yaml(path):
    """Read a YAML file and make sure the top level is a mapping."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level.")
    return data


def load_scan_areas():
    """Read config/scan_areas.yml and validate every area."""
    data = load_yaml(SCAN_AREAS_FILE)
    areas = data.get("areas")
    if not isinstance(areas, list) or not areas:
        raise ConfigError("scan_areas.yml must contain a non-empty 'areas' list.")

    labels = set()
    for index, area in enumerate(areas):
        where = f"scan_areas.yml, area #{index + 1}"
        if not isinstance(area, dict):
            raise ConfigError(f"{where}: each area must be a mapping (label, latitude, ...).")
        for key in ("label", "latitude", "longitude", "radius_meters"):
            if key not in area:
                raise ConfigError(f"{where}: missing required key '{key}'.")

        label = area["label"]
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"{where}: 'label' must be a non-empty text value.")
        if label in labels:
            raise ConfigError(f"scan_areas.yml: duplicate area label '{label}'.")
        labels.add(label)

        for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            value = area[key]
            if not isinstance(value, (int, float)) or not (low <= value <= high):
                raise ConfigError(
                    f"{where} ('{label}'): '{key}' must be a number between {low} and {high}."
                )

        radius = area["radius_meters"]
        if not isinstance(radius, (int, float)) or not (0 < radius <= MAX_RADIUS_METERS):
            raise ConfigError(
                f"{where} ('{label}'): 'radius_meters' must be a number between "
                f"1 and {MAX_RADIUS_METERS} (Google API limit)."
            )
    return areas


def load_categories():
    """Read config/categories.yml and validate every category."""
    data = load_yaml(CATEGORIES_FILE)
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ConfigError("categories.yml must contain a non-empty 'categories' list.")

    labels = set()
    required = (
        "label",
        "included_types",
        "appointment_based",
        "quote_based",
        "menu_based",
        "recommended_offer",
    )
    for index, category in enumerate(categories):
        where = f"categories.yml, category #{index + 1}"
        if not isinstance(category, dict):
            raise ConfigError(f"{where}: each category must be a mapping (label, included_types, ...).")
        for key in required:
            if key not in category:
                raise ConfigError(f"{where}: missing required key '{key}'.")

        label = category["label"]
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"{where}: 'label' must be a non-empty text value.")
        if label in labels:
            raise ConfigError(f"categories.yml: duplicate category label '{label}'.")
        labels.add(label)

        types = category["included_types"]
        if (
            not isinstance(types, list)
            or not types
            or not all(isinstance(t, str) and t.strip() for t in types)
        ):
            raise ConfigError(
                f"{where} ('{label}'): 'included_types' must be a non-empty list of "
                "Google place type strings."
            )

        for key in ("appointment_based", "quote_based", "menu_based"):
            if not isinstance(category[key], bool):
                raise ConfigError(f"{where} ('{label}'): '{key}' must be true or false.")
    return categories


def load_json(path, default=_MISSING):
    """Read a JSON file. If it does not exist, return the default (if given)."""
    if not path.exists():
        if default is not _MISSING:
            return default
        raise ConfigError(f"File not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError) as exc:
        raise ConfigError(f"Could not read {path.name}: {exc}") from exc


def save_json(path, data):
    """Write data as pretty-printed JSON, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
