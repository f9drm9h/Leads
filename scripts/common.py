"""Shared helpers for the local-business-leads scripts.

All three scripts (scan_places, score_leads, export_report) import from this
module so file paths, config loading and JSON handling live in one place.
"""

import json
import re
import sys
import unicodedata
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
# Private sales research lives here. The directory is gitignored — nothing
# in it may ever be committed or copied into the public reports/ pages.
PRIVATE_DIR = PROJECT_ROOT / "private"

SCAN_AREAS_FILE = CONFIG_DIR / "scan_areas.yml"
CATEGORIES_FILE = CONFIG_DIR / "categories.yml"
SCAN_MATRIX_FILE = CONFIG_DIR / "scan_matrix.yml"
MANUAL_CHECKS_FILE = CONFIG_DIR / "manual_checks.yml"
# Gitignored twin of manual_checks.yml: same format, but entries here may
# carry evidence notes and sales remarks that must never be committed.
MANUAL_CHECKS_LOCAL_FILE = CONFIG_DIR / "manual_checks.local.yml"

# Working files produced by the pipeline. This is short-term research data,
# not a permanent database — refresh it before every outreach round.
SCAN_RESULTS_FILE = DATA_DIR / "scan_results.json"
SCORED_LEADS_FILE = DATA_DIR / "leads_scored.json"
# Local log of Places API usage, one entry per scan run (gitignored with the
# rest of data/). Lets you check how many requests were made this month.
REQUEST_LOG_FILE = DATA_DIR / "request_log.json"

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

        # Optional human-readable neighborhood name; defaults to the label.
        name = area.setdefault("name", label)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{where} ('{label}'): 'name' must be a non-empty text value.")

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

        # Optional keys used by the category-confidence filter. They default
        # to empty / false so older config files keep working unchanged.
        for key in (
            "excluded_name_keywords",
            "excluded_types",
            "also_match_types",
            "included_name_keywords",
        ):
            value = category.setdefault(key, [])
            if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value
            ):
                raise ConfigError(
                    f"{where} ('{label}'): '{key}' must be a list of text values "
                    "(an empty list [] is fine)."
                )
        if not isinstance(category.setdefault("question_heavy", False), bool):
            raise ConfigError(f"{where} ('{label}'): 'question_heavy' must be true or false.")
    return categories


def load_scan_matrix(areas, categories):
    """Read config/scan_matrix.yml -> list of (area, category) dict pairs.

    Every area/category label in the matrix must exist in the main configs,
    and no (area x category) pair may appear twice.
    """
    data = load_yaml(SCAN_MATRIX_FILE)
    entries = data.get("matrix")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("scan_matrix.yml must contain a non-empty 'matrix' list.")

    areas_by_label = {area["label"]: area for area in areas}
    categories_by_label = {cat["label"]: cat for cat in categories}

    pairs, seen = [], set()
    for index, entry in enumerate(entries):
        where = f"scan_matrix.yml, entry #{index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: each entry must be a mapping (area, categories).")
        area_label = entry.get("area")
        if area_label not in areas_by_label:
            raise ConfigError(
                f"{where}: unknown area '{area_label}'. "
                "Labels must exist in scan_areas.yml."
            )
        category_labels = entry.get("categories")
        if not isinstance(category_labels, list) or not category_labels:
            raise ConfigError(f"{where} ('{area_label}'): 'categories' must be a non-empty list.")
        for category_label in category_labels:
            if category_label not in categories_by_label:
                raise ConfigError(
                    f"{where} ('{area_label}'): unknown category '{category_label}'. "
                    "Labels must exist in categories.yml."
                )
            pair_key = (area_label, category_label)
            if pair_key in seen:
                raise ConfigError(
                    f"{where}: duplicate pair '{area_label}' x '{category_label}'."
                )
            seen.add(pair_key)
            pairs.append((areas_by_label[area_label], categories_by_label[category_label]))
    return pairs


# Values you may record in config/manual_checks.yml after checking a business
# online by hand. These are the ONLY sources of "verified" presence knowledge —
# the tool never scrapes or probes anything itself.
ONLINE_PRESENCE_MANUAL_VALUES = (
    "weak_or_missing",
    "has_social_presence",
    "has_booking_presence",
    "has_directory_presence",
    "has_website",
)


def _load_manual_checks_file(path):
    """Read one manual-checks file -> {place_id: {online_presence, note}}."""
    if not path.exists():
        return {}
    data = load_yaml(path)
    checks = data.get("checks") or []
    if not isinstance(checks, list):
        raise ConfigError(f"{path.name}: 'checks' must be a list.")

    result = {}
    for index, entry in enumerate(checks):
        where = f"{path.name}, check #{index + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: each check must be a mapping (place_id, online_presence).")
        place_id = entry.get("place_id")
        if not isinstance(place_id, str) or not place_id.strip():
            raise ConfigError(f"{where}: 'place_id' must be a non-empty text value.")
        presence = entry.get("online_presence")
        if presence not in ONLINE_PRESENCE_MANUAL_VALUES:
            raise ConfigError(
                f"{where}: 'online_presence' must be one of: "
                + ", ".join(ONLINE_PRESENCE_MANUAL_VALUES)
            )
        note = entry.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise ConfigError(f"{where}: 'note' must be text.")
        result[place_id.strip()] = {"online_presence": presence, "note": note.strip()}
    return result


def load_manual_checks():
    """Read the manual verification results from BOTH manual-checks files.

    config/manual_checks.yml is COMMITTED: it may only hold neutral status
    values (place_id + online_presence, no notes). config/manual_checks.local.yml
    is GITIGNORED: same format, but entries there may carry evidence notes and
    sales remarks. Local entries override committed ones for the same place id.
    Both files are optional; empty/missing files mean nothing verified yet.
    """
    committed = _load_manual_checks_file(MANUAL_CHECKS_FILE)
    noted = [pid for pid, check in committed.items() if check.get("note")]
    if noted:
        print(
            "WARNING: config/manual_checks.yml is committed to the public repo but "
            f"contains notes on {len(noted)} check(s). Move evidence/sales notes to "
            "config/manual_checks.local.yml (gitignored) and keep only place_id + "
            "online_presence in the committed file.",
            file=sys.stderr,
        )
    combined = dict(committed)
    combined.update(_load_manual_checks_file(MANUAL_CHECKS_LOCAL_FILE))
    return combined


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


# ---------------------------------------------------------------------------
# Text normalization and brand keys.
#
# A "brand key" is a simplified version of a business name used to group
# multiple Google profiles of the same brand (branches) into one cluster.
# The rules are deliberately conservative: two leads only cluster together
# when their normalized names are IDENTICAL — there is no fuzzy matching,
# because over-merging unrelated businesses is worse than missing a branch.
# ---------------------------------------------------------------------------
_PARENS_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

# Legal/company suffixes stripped from the END of a name (longest first).
# Covers the dotted forms too: after punctuation removal "S.R.L." -> "s r l".
_COMPANY_SUFFIX_SEQUENCES = [
    ("c", "por", "a"),
    ("c", "x", "a"),
    ("e", "i", "r", "l"),
    ("s", "r", "l"),
    ("s", "a"),
    ("eirl",),
    ("srl",),
    ("sa",),
    ("inc",),
    ("ltd",),
    ("llc",),
    ("rd",),
]

# Tokens that describe WHAT a business is rather than WHO it is. A brand key
# made up entirely of these (e.g. "salon de belleza") identifies nothing, so
# a multi-member cluster with such a key is treated as UNCERTAIN — flagged
# for manual review instead of being trusted as one brand.
GENERIC_BRAND_TOKENS = {
    # connectors / filler
    "de", "del", "la", "el", "los", "las", "y", "e", "d", "mi", "don", "dona",
    "the", "and", "in", "no", "num",
    # beauty
    "barberia", "barber", "barbershop", "salon", "salones", "belleza",
    "beauty", "spa", "nails", "nail", "unas", "peluqueria", "estetica",
    "estilo", "estilos", "look", "hair", "studio", "estudio",
    # food
    "restaurante", "restaurant", "comedor", "cafeteria", "cafe", "coffee",
    "pizzeria", "pizza", "pica", "pollo", "bar", "grill", "food", "comida",
    "cocina", "panaderia", "reposteria", "bakery", "drink", "drinks",
    # auto
    "taller", "mecanica", "repuestos", "gomera", "gomas", "auto", "autos",
    "car", "wash", "carwash", "lavado", "autolavado", "detailing", "adornos",
    # phones / electronics
    "celulares", "celular", "cell", "phone", "phones", "movil", "moviles",
    "electronica", "electronics", "repair", "reparacion", "reparaciones",
    "tecnologia", "tech", "comunicaciones",
    # events
    "eventos", "evento", "fiestas", "party", "decoracion", "decoraciones",
    "floristeria", "flores", "catering",
    # commerce / places
    "colmado", "supermercado", "market", "minimarket", "tienda", "store",
    "shop", "boutique", "centro", "plaza", "casa", "grupo", "group",
    "servicio", "servicios", "multiservicios", "soluciones", "shopping",
    "dominicana", "dominicano", "nacional", "internacional", "express",
}


def strip_accents(text):
    """Remove accents: 'peluquería' -> 'peluqueria'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text):
    """Lowercase, remove accents, turn punctuation into spaces, collapse spaces.

    Used for keyword matching against business names.
    """
    text = strip_accents(str(text or "").lower())
    return _NON_ALNUM_RE.sub(" ", text).strip()


def make_brand_key(name):
    """Build the normalized brand key for a business name.

    Steps: lowercase -> remove accents -> drop '(...)' branch hints ->
    punctuation to spaces -> strip trailing company suffixes (SRL, S.A.,
    RD, ...) -> collapse whitespace. The original name is never modified;
    this only derives a grouping key from a copy.
    """
    text = strip_accents(str(name or "").lower())
    text = _PARENS_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    tokens = text.split()

    stripped = True
    while stripped and tokens:
        stripped = False
        for seq in _COMPANY_SUFFIX_SEQUENCES:
            n = len(seq)
            # '>' (not '>=') so a name that IS just a suffix keeps its name.
            if len(tokens) > n and tuple(tokens[-n:]) == seq:
                tokens = tokens[:-n]
                stripped = True
                break
    return " ".join(tokens)


def is_generic_brand_key(brand_key):
    """True when a brand key says nothing distinctive about the business.

    'salon de belleza' -> True (all generic words); 'salon anyelina' -> False.
    Multi-member clusters with a generic key are flagged for manual review.
    """
    tokens = brand_key.split()
    if not tokens:
        return True
    return all(token in GENERIC_BRAND_TOKENS or token.isdigit() for token in tokens)


# Extra descriptor words stripped (on top of GENERIC_BRAND_TOKENS) when
# building the CORE brand key used to spot likely same-brand rows:
# "MONTIBELLO Hair Lounge and MedSpa" and "Montibello" both reduce to
# "montibello". Core keys only FLAG possible matches — they never auto-merge.
CORE_DESCRIPTOR_TOKENS = GENERIC_BRAND_TOKENS | {
    "lounge", "medspa", "med", "by", "service", "services", "and",
}


def make_core_brand_key(name):
    """Brand key with generic descriptor words removed.

    Used only to flag possible_brand_match candidates for manual review;
    actual clustering still requires the full brand keys to be identical,
    so unrelated businesses are never merged automatically.
    """
    tokens = [
        token
        for token in make_brand_key(name).split()
        if token not in CORE_DESCRIPTOR_TOKENS and not token.isdigit()
    ]
    return " ".join(tokens)


def name_keyword_hit(name, keywords):
    """Return the first keyword that matches the business name, else None.

    Keywords match whole words in the normalized name ('auto' does not match
    'autorizado'). A trailing '*' makes it a prefix match ('auto*' matches
    'autopartes'). Multi-word keywords match as a phrase ('aire acondicionado').
    """
    haystack = normalize_text(name)
    if not haystack:
        return None
    for keyword in keywords or []:
        needle = normalize_text(keyword.rstrip("*"))
        if not needle:
            continue
        if keyword.rstrip().endswith("*"):
            pattern = r"(?:^|\s)" + re.escape(needle) + r"[0-9a-z]*"
        else:
            pattern = r"(?:^|\s)" + re.escape(needle) + r"(?:\s|$)"
        if re.search(pattern, haystack):
            return keyword
    return None
