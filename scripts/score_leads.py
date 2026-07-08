"""Score scanned businesses: cluster brands, judge data quality, pick offers.

Reads  data/scan_results.json    (produced by scan_places.py)
Reads  config/manual_checks.yml  (your manual online-presence verifications)
Writes data/leads_scored.json    (leads + brand cluster + website status +
                                  online presence + lead type + score)

What happens to each lead:
  1. Duplicate place ids are collapsed into one row (source lists merged).
  2. A conservative brand_key groups branches of the same brand together;
     a looser "core" key flags LIKELY same-brand rows (possible_brand_match)
     without ever auto-merging them.
  3. Website status looks across the whole brand cluster. A missing
     websiteUri only means the GOOGLE PROFILE returned no website — the
     business may still have Instagram/booking/directory presence, so
     unverified leads are POTENTIAL_WEBSITE_LEAD, never NEW_WEBSITE_LEAD.
  4. Manual checks from config/manual_checks.yml upgrade or downgrade leads:
     only a hand-verified weak_or_missing presence makes a NEW_WEBSITE_LEAD.
  5. Category confidence checks the Google place types (and name keywords)
     against the category that found the lead, and names that look like a
     plaza/mall/building are flagged as bad category matches.
  6. Two separate priorities are attached: manual_verification_priority
     ("check this lead first") and sales_priority ("contact this lead
     first"). Sales priority is never high while the online presence is
     still unverified.

Usage:
    py scripts/score_leads.py
    py scripts/score_leads.py --top 20     (print more of the ranking)
"""

import argparse

from common import (
    SCAN_RESULTS_FILE,
    SCORED_LEADS_FILE,
    ConfigError,
    die,
    is_generic_brand_key,
    load_categories,
    load_json,
    load_manual_checks,
    make_brand_key,
    make_core_brand_key,
    name_keyword_hit,
    save_json,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Scoring rules. Tweak these numbers freely — they are the whole model.
# A high score means "worth manually verifying first, probably worth money".
# There is deliberately NO big bonus for a missing websiteUri anymore:
# only a manual verification can prove weak online presence.
# ---------------------------------------------------------------------------
POINTS_PROFILE_NO_WEBSITE = 15    # Google profile returned no websiteUri
POINTS_VERIFIED_WEAK = 25         # manually verified weak/missing presence
POINTS_HAS_PHONE = 15
POINTS_GOOD_RATING = 15           # rating >= GOOD_RATING_MIN
POINTS_MANY_REVIEWS = 15          # review_count >= MANY_REVIEWS_MIN
POINTS_HIGH_CONFIDENCE = 10       # place types clearly match the category
POINTS_SERVICE_CATEGORY = 10      # appointment-, quote- or menu-based category
POINTS_OPERATIONAL = 5
PENALTY_BRAND_HAS_WEBSITE = -30   # another location of this brand has a site
PENALTY_OTHER_PRESENCE = -15      # verified social/booking/directory presence
PENALTY_LIKELY_CHAIN = -25        # franchise / 3+ locations
PENALTY_POSSIBLE_MULTI = -20      # possible multi-location brand (2 locations
                                  # or a likely name match) — not applied on
                                  # top of PENALTY_LIKELY_CHAIN
PENALTY_BAD_CATEGORY = -25        # name says plaza/mall/building, types don't
                                  # prove a real business in the niche
PENALTY_LOW_CONFIDENCE = -25      # probably not the category we scanned for
PENALTY_CLOSED_PERMANENTLY = -50
PENALTY_CLOSED_TEMPORARILY = -25

GOOD_RATING_MIN = 4.0
MANY_REVIEWS_MIN = 25

BUSY_BUSINESS_MIN_REVIEWS = 50       # "complex enough" for an FAQ chatbot
SIGNIFICANT_BRANCH_MIN_REVIEWS = 25  # busy branch -> worth its own branch page
LIKELY_CHAIN_MIN_LOCATIONS = 3       # 3+ scanned locations = likely a chain
MAX_OFFERS_PER_LEAD = 2              # keep the pitch simple
MAX_LISTED_BRANCH_LOCATIONS = 5      # cap the same_brand_locations list
MAX_LISTED_BRAND_MATCHES = 5         # cap the possible_brand_match list
MIN_CORE_KEY_LENGTH = 3              # shorter core keys are too weak to match on

# Website status values — all of them describe the GOOGLE PROFILE only.
STATUS_HAS_WEBSITE = "has_website"
STATUS_BRAND_ELSEWHERE = "brand_has_website_elsewhere"
STATUS_ALL_MISSING = "all_locations_missing_website"
STATUS_NEEDS_REVIEW = "needs_manual_review"

# online_presence_status values (see README). Only manual checks can set the
# "verified" values — the tool never probes Instagram/Facebook/etc. itself.
PRESENCE_UNKNOWN = "unknown_not_checked"
PRESENCE_WEAK = "weak_or_missing"
PRESENCE_SOCIAL = "has_social_presence"
PRESENCE_BOOKING = "has_booking_presence"
PRESENCE_DIRECTORY = "has_directory_presence"
PRESENCE_WEBSITE = "has_website"
PRESENCE_NEEDS_REVIEW = "needs_manual_review"

VERIFIED_OTHER_PRESENCE = {PRESENCE_SOCIAL, PRESENCE_BOOKING, PRESENCE_DIRECTORY}

# Lead types, roughly from most to least actionable.
LEAD_NEW_WEBSITE = "NEW_WEBSITE_LEAD"          # ONLY after manual verification
LEAD_POTENTIAL_WEBSITE = "POTENTIAL_WEBSITE_LEAD"  # profile lacks a site, unverified
LEAD_GBP_CLEANUP = "GBP_CLEANUP_LEAD"
LEAD_BRANCH_PAGE = "BRANCH_PAGE_LEAD"
LEAD_MENU_PAGE = "MENU_PAGE_LEAD"
LEAD_QUOTE_FORM = "QUOTE_FORM_LEAD"
LEAD_APPOINTMENT = "APPOINTMENT_PAGE_LEAD"
LEAD_CHATBOT = "CHATBOT_CANDIDATE"
LEAD_MULTI_LOCATION = "MULTI_LOCATION_BRAND_REVIEW"
LEAD_BAD_CATEGORY = "BAD_CATEGORY_MATCH"
LEAD_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
LEAD_LOW_PRIORITY = "LOW_PRIORITY"

# Words that suggest the profile is a building/location, not a business.
LOCATION_NAME_KEYWORDS = [
    "plaza",
    "mall",
    "centro comercial",
    "edificio",
    "torre",
    "square",
]

# Banks, arenas, government offices and other institutional places are not
# side-hustle sales targets: even when their profile data is poor, the
# decision maker is a head office or the state, not a walk-in owner.
# Both lists are matched locally only — nothing is probed or scraped.
INSTITUTIONAL_PLACE_TYPES = {
    "bank", "atm", "stadium", "arena", "sports_complex", "shopping_mall",
    "city_hall", "courthouse", "embassy", "local_government_office",
    "government_office", "police", "fire_station", "post_office",
}
INSTITUTIONAL_NAME_KEYWORDS = [
    "banco", "bank", "arena", "estadio", "stadium", "coliseo",
    "ministerio", "ayuntamiento", "alcaldia", "gobierno",
    "embajada", "consulado",
]

# If a brand key contains any of these, treat it as a known chain/franchise:
# the parent company almost certainly has a website even if no scanned
# profile lists one, so "no website found" is never trusted automatically.
KNOWN_FRANCHISE_KEYWORDS = [
    "claro", "altice", "viva ", "orange", "banreservas", "banco popular",
    "banco bhd", "scotiabank", "western union", "caribe express", "vimenca",
    "mcdonald", "burger king", "kfc", "domino", "pizza hut", "subway",
    "wendy", "taco bell", "krispy kreme", "dunkin", "starbucks",
    "la sirena", "jumbo", "bravo", "aliss", "plaza lama", "ikea", "sirena",
    "total ", "shell ", "texaco", "esso",
]

# Types Google attaches to almost anything — they carry no category signal.
GENERIC_PLACE_TYPES = {"establishment", "point_of_interest", "store", "food"}

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def upgrade_and_dedupe(raw_leads):
    """Collapse duplicate place ids into one row and fill in missing fields.

    scan_places.py already merges by id, but this guards against hand-edited
    or older data files. The newest scan of a place wins; the lists of source
    areas/categories that found it are merged.
    """
    merged = {}
    duplicates = 0
    for lead in raw_leads:
        place_id = lead.get("id")
        if not place_id:
            continue
        if not isinstance(lead.get("source_areas"), list):
            lead["source_areas"] = [lead["source_area"]] if lead.get("source_area") else []
        if not isinstance(lead.get("source_categories"), list):
            lead["source_categories"] = (
                [lead["source_category"]] if lead.get("source_category") else []
            )
        if not isinstance(lead.get("types"), list):
            lead["types"] = []

        previous = merged.get(place_id)
        if previous is None:
            merged[place_id] = lead
            continue
        duplicates += 1
        newer, older = (
            (lead, previous)
            if (lead.get("scanned_at") or "") >= (previous.get("scanned_at") or "")
            else (previous, lead)
        )
        newer["source_areas"] = list(
            dict.fromkeys(older["source_areas"] + newer["source_areas"])
        )
        newer["source_categories"] = list(
            dict.fromkeys(older["source_categories"] + newer["source_categories"])
        )
        merged[place_id] = newer
    return list(merged.values()), duplicates


def franchise_keyword_hit(brand_key):
    """Return the matching franchise keyword, or None.

    Keywords match as substrings ('mcdonald' catches 'mcdonald s'); a keyword
    with a trailing space only matches at the start of a word, so short names
    like 'viva ' don't fire inside longer unrelated words.
    """
    padded = f" {brand_key} "
    for keyword in KNOWN_FRANCHISE_KEYWORDS:
        needle = f" {keyword}" if keyword.endswith(" ") else keyword
        if needle in padded:
            return keyword.strip()
    return None


def build_clusters(leads):
    """Group leads by brand key and attach the cluster fields to every lead."""
    groups = {}
    for lead in leads:
        key = make_brand_key(lead.get("name", ""))
        if not key:
            key = f"(unnamed) {lead['id']}"  # can't cluster without a usable name
        lead["brand_key"] = key
        groups.setdefault(key, []).append(lead)

    clusters = {}
    for index, key in enumerate(sorted(groups), start=1):
        members = groups[key]
        size = len(members)
        franchise = franchise_keyword_hit(key)
        clusters[key] = {
            "cluster_id": f"c{index:03d}",
            "size": size,
            # Uncertain = we can't trust that same-name places are one brand
            # (generic names like "salon de belleza"), or we know the brand is
            # a chain whose real website probably wasn't in our scan at all.
            "uncertain": (size > 1 and is_generic_brand_key(key)) or bool(franchise),
            "generic_name": size > 1 and is_generic_brand_key(key),
            "franchise_keyword": franchise,
            "any_website": next((m["website"] for m in members if m.get("website")), ""),
            "likely_chain": size >= LIKELY_CHAIN_MIN_LOCATIONS or bool(franchise),
        }

    for lead in leads:
        cluster = clusters[lead["brand_key"]]
        others = [m for m in groups[lead["brand_key"]] if m is not lead]
        listed = [
            " — ".join(part for part in (m.get("name", "?"), m.get("address", "")) if part)
            for m in others[:MAX_LISTED_BRANCH_LOCATIONS]
        ]
        if len(others) > MAX_LISTED_BRANCH_LOCATIONS:
            listed.append(f"(+{len(others) - MAX_LISTED_BRANCH_LOCATIONS} more)")

        lead["cluster_id"] = cluster["cluster_id"]
        lead["cluster_size"] = cluster["size"]
        lead["is_likely_chain"] = cluster["likely_chain"]
        lead["other_locations_count"] = cluster["size"] - 1
        lead["same_brand_locations"] = listed
        lead["cluster_uncertain"] = cluster["uncertain"]
    return clusters


def find_possible_brand_matches(leads):
    """Flag rows whose CORE brand keys collide across different clusters.

    'Montibello' and 'MONTIBELLO Hair Lounge and MedSpa' have different
    strict brand keys (so they are NOT auto-merged), but the same core key
    'montibello' — each gets the other listed in possible_brand_match so a
    human can decide whether they are one brand.
    """
    core_groups = {}
    for lead in leads:
        core = make_core_brand_key(lead.get("name", ""))
        lead["core_brand_key"] = core
        if len(core) >= MIN_CORE_KEY_LENGTH and not is_generic_brand_key(core):
            core_groups.setdefault(core, []).append(lead)

    for lead in leads:
        lead["possible_brand_match"] = []
        group = core_groups.get(lead["core_brand_key"], [])
        # Only interesting when the collision crosses cluster boundaries —
        # rows in the same cluster are already grouped as branches.
        others = [m for m in group if m["cluster_id"] != lead["cluster_id"]]
        if not others:
            continue
        names = list(dict.fromkeys(m.get("name", "?") for m in others))
        if len(names) > MAX_LISTED_BRAND_MATCHES:
            names = names[:MAX_LISTED_BRAND_MATCHES] + [
                f"(+{len(names) - MAX_LISTED_BRAND_MATCHES} more)"
            ]
        lead["possible_brand_match"] = names

    for lead in leads:
        lead["is_possible_chain"] = (
            lead["cluster_size"] >= 2
            or bool(lead["possible_brand_match"])
            or lead["is_likely_chain"]
        )


def assign_website_status(lead, cluster):
    """Set the website flags plus the single website_status value.

    Every value describes the GOOGLE PROFILE(S) we scanned — a missing
    websiteUri does NOT mean the business has no online presence at all.
    """
    has_site = bool(lead.get("website"))
    lead["has_website"] = has_site
    lead["missing_profile_website"] = not has_site
    lead["brand_has_website_elsewhere"] = False
    lead["all_locations_missing_website"] = False
    lead["needs_manual_review"] = False
    lead["brand_website_example"] = ""

    if has_site:
        lead["website_status"] = STATUS_HAS_WEBSITE
    elif cluster["uncertain"] or lead["possible_brand_match"]:
        lead["website_status"] = STATUS_NEEDS_REVIEW
        lead["needs_manual_review"] = True
        if cluster["any_website"]:
            lead["brand_website_example"] = cluster["any_website"]
    elif cluster["any_website"]:
        lead["website_status"] = STATUS_BRAND_ELSEWHERE
        lead["brand_has_website_elsewhere"] = True
        lead["brand_website_example"] = cluster["any_website"]
    else:
        lead["website_status"] = STATUS_ALL_MISSING
        lead["all_locations_missing_website"] = True


def match_types(place_types, patterns):
    """Place types that match a list of patterns.

    A pattern is an exact type name, or uses '*' at one end as a wildcard:
    '*_restaurant' matches 'mexican_restaurant', 'car_*' matches 'car_wash'.
    """
    matched = set()
    for pattern in patterns:
        if pattern.startswith("*"):
            matched.update(t for t in place_types if t.endswith(pattern[1:]))
        elif pattern.endswith("*"):
            matched.update(t for t in place_types if t.startswith(pattern[:-1]))
        elif pattern in place_types:
            matched.add(pattern)
    return matched


def lead_place_types(lead):
    """All Google types we know for a lead (types list + primary type)."""
    types = set(lead.get("types") or [])
    if lead.get("primary_type"):
        types.add(lead["primary_type"])
    return types


def category_confidence(lead, category):
    """How sure are we this lead really belongs to the scanned category?

    Returns (level, reason): level is 'high', 'medium' or 'low'.
    High requires the Google place types to match the category; excluded
    types/keywords push a lead down unless the types clearly support it.
    """
    types = lead_place_types(lead)

    type_match = match_types(types, category["included_types"] + category["also_match_types"])
    type_conflict = match_types(types, category["excluded_types"])
    bad_keyword = name_keyword_hit(lead.get("name", ""), category["excluded_name_keywords"])
    good_keyword = name_keyword_hit(lead.get("name", ""), category["included_name_keywords"])

    if type_match:
        if bad_keyword:
            return "medium", f"name contains excluded keyword '{bad_keyword}'"
        if type_conflict:
            return "medium", f"mixed place types ({', '.join(sorted(type_conflict))})"
        return "high", ""
    if type_conflict:
        return "low", f"place types point elsewhere ({', '.join(sorted(type_conflict))})"
    if bad_keyword:
        return "low", f"name contains excluded keyword '{bad_keyword}'"
    if not types:
        return "medium", "no place types stored — re-scan to fetch them"
    if types <= GENERIC_PLACE_TYPES:
        return "medium", "only generic place types available"
    if good_keyword:
        return "medium", f"name suggests the category ('{good_keyword}') but types do not"
    return "low", f"place types do not match category ({', '.join(sorted(types)[:4])})"


def pick_category(lead, categories):
    """Choose the best-fitting category among those that found this lead."""
    labels = list(lead.get("source_categories") or [])
    last_scanned = lead.get("source_category")
    if last_scanned in labels:  # evaluate the most recent one first (tie-break)
        labels.remove(last_scanned)
        labels.insert(0, last_scanned)

    best_label, best_level, best_reason = None, None, ""
    for label in labels:
        category = categories.get(label)
        if category is None:
            continue
        level, reason = category_confidence(lead, category)
        if best_level is None or CONFIDENCE_RANK[level] > CONFIDENCE_RANK[best_level]:
            best_label, best_level, best_reason = label, level, reason

    if best_label is None:
        # Category was renamed/removed from the config since the scan.
        return None, last_scanned or "", "medium", "category not found in categories.yml"
    return categories[best_label], best_label, best_level, best_reason


def detect_bad_category(lead, category):
    """Does the NAME say this is a plaza/mall/building rather than a business?

    Returns (confirmed, suspect, keyword):
      confirmed - location word in the name and NO type evidence for the
                  category -> BAD_CATEGORY_MATCH.
      suspect   - location word in the name but the types do claim the
                  category (e.g. a plaza profile typed beauty_salon because
                  of its tenants) -> needs manual review, no auto-penalty.
    """
    keyword = name_keyword_hit(lead.get("name", ""), LOCATION_NAME_KEYWORDS)
    if not keyword:
        return False, False, None
    if category is None:
        return True, False, keyword
    type_proof = match_types(
        lead_place_types(lead),
        category["included_types"] + category["also_match_types"],
    )
    if type_proof:
        return False, True, keyword
    return True, False, keyword


def resolve_online_presence(lead, manual):
    """Combine profile data + manual verification into online_presence_status."""
    if manual:
        return manual["online_presence"]
    if lead["has_website"]:
        return PRESENCE_WEBSITE
    if (
        lead["website_status"] == STATUS_NEEDS_REVIEW
        or lead["possible_brand_match"]
        or lead["bad_category_match"]
        or lead["bad_category_suspect"]
    ):
        return PRESENCE_NEEDS_REVIEW
    return PRESENCE_UNKNOWN


def pick_lead_type(lead, category):
    """Classify what kind of opportunity this lead is."""
    status = lead.get("business_status", "")
    if status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return LEAD_LOW_PRIORITY
    if lead["bad_category_match"]:
        return LEAD_BAD_CATEGORY
    if lead["category_confidence"] == "low" or lead["bad_category_suspect"]:
        return LEAD_MANUAL_REVIEW

    profile_or_verified_site = lead["has_website"] or (
        lead["online_presence_status"] == PRESENCE_WEBSITE
    )
    if not profile_or_verified_site:
        if lead["brand_has_website_elsewhere"]:
            if (lead.get("review_count") or 0) >= SIGNIFICANT_BRANCH_MIN_REVIEWS:
                return LEAD_BRANCH_PAGE
            return LEAD_GBP_CLEANUP
        if (
            lead["possible_brand_match"]
            or lead["is_likely_chain"]
            or (lead["cluster_size"] >= 2 and not lead["cluster_uncertain"])
        ):
            return LEAD_MULTI_LOCATION
        if lead["website_status"] == STATUS_NEEDS_REVIEW:
            return LEAD_MANUAL_REVIEW
        if lead["online_presence_status"] == PRESENCE_WEAK:
            return LEAD_NEW_WEBSITE  # only reachable via manual verification
        return LEAD_POTENTIAL_WEBSITE

    # The business has a website (on the profile, or found manually) —
    # look for optimization work instead of a new site.
    if category:
        if category["menu_based"]:
            return LEAD_MENU_PAGE
        if category["quote_based"]:
            return LEAD_QUOTE_FORM
        if category["appointment_based"]:
            return LEAD_APPOINTMENT
        if category["question_heavy"] and (lead.get("review_count") or 0) >= BUSY_BUSINESS_MIN_REVIEWS:
            return LEAD_CHATBOT
    return LEAD_LOW_PRIORITY


def score_lead(lead, category):
    """Apply the scoring formula to one lead. Returns an integer score."""
    score = 0
    rating = lead.get("rating")
    review_count = lead.get("review_count") or 0
    status = lead.get("business_status", "")
    presence = lead["online_presence_status"]

    if lead["missing_profile_website"]:
        score += POINTS_PROFILE_NO_WEBSITE
    if presence == PRESENCE_WEAK:
        score += POINTS_VERIFIED_WEAK
    if presence in VERIFIED_OTHER_PRESENCE or (
        presence == PRESENCE_WEBSITE and not lead["has_website"]
    ):
        score += PENALTY_OTHER_PRESENCE
    if lead.get("phone"):
        score += POINTS_HAS_PHONE
    if rating is not None and rating >= GOOD_RATING_MIN:
        score += POINTS_GOOD_RATING
    if review_count >= MANY_REVIEWS_MIN:
        score += POINTS_MANY_REVIEWS
    if lead["category_confidence"] == "high":
        score += POINTS_HIGH_CONFIDENCE
    if category and (
        category["appointment_based"] or category["quote_based"] or category["menu_based"]
    ):
        score += POINTS_SERVICE_CATEGORY
    if status == "OPERATIONAL":
        score += POINTS_OPERATIONAL
    elif status == "CLOSED_PERMANENTLY":
        score += PENALTY_CLOSED_PERMANENTLY
    elif status == "CLOSED_TEMPORARILY":
        score += PENALTY_CLOSED_TEMPORARILY
    if lead["brand_has_website_elsewhere"]:
        score += PENALTY_BRAND_HAS_WEBSITE
    if lead["category_confidence"] == "low":
        score += PENALTY_LOW_CONFIDENCE
    if lead["bad_category_match"]:
        score += PENALTY_BAD_CATEGORY
    if lead["is_likely_chain"]:
        score += PENALTY_LIKELY_CHAIN
    elif lead["is_possible_chain"]:
        score += PENALTY_POSSIBLE_MULTI
    return score


def pick_verification_priority(lead):
    """How urgently should a human verify this lead's real online presence?

    high   = promising unverified lead — check these first
    medium = worth checking, but weaker or murkier signals
    low    = already verified, already has a site, or not worth the time
    """
    if lead["manually_verified"]:
        return "low"
    if lead["has_website"]:
        return "low"
    if lead["lead_type"] in (LEAD_LOW_PRIORITY, LEAD_BAD_CATEGORY):
        return "low"
    if lead["category_confidence"] == "low":
        return "low"
    if (
        lead["lead_type"] == LEAD_POTENTIAL_WEBSITE
        and lead["category_confidence"] == "high"
        and lead.get("business_status") == "OPERATIONAL"
    ):
        return "high"
    return "medium"


def is_institutional_place(lead):
    """Bank / arena / government office / mall / plaza / commercial building?

    Detected from the Google place types plus whole-word name keywords
    (accent-insensitive). Verify priority is untouched by this — a chain or
    institution can still be worth checking for data quality.
    """
    if lead_place_types(lead) & INSTITUTIONAL_PLACE_TYPES:
        return True
    keywords = INSTITUTIONAL_NAME_KEYWORDS + LOCATION_NAME_KEYWORDS
    return name_keyword_hit(lead.get("name", ""), keywords) is not None


def pick_sales_priority(lead):
    """How urgently should you CONTACT this lead? (not the same question!)

    Verification priority answers "check this lead first"; sales priority
    answers "contact this lead first". A promising-but-unchecked lead is
    HIGH verification priority yet only MEDIUM sales priority, because it
    may have strong Instagram/booking/brand presence the Places API can't
    see. Only a manual weak_or_missing verification unlocks "high".

    high   = verified weak/missing online presence — the pitch is solid
    medium = good target on paper but presence unconfirmed, or a concrete
             optimization offer (profile cleanup, menu/booking page, ...)
    low    = unresolved identity/brand questions, verified presence that
             makes the pitch weak, chains/franchises with a website, or
             institutional places that scored high category confidence
    skip   = probably not a real business in this category, or a bank/
             arena/government/mall-type place — not a side-hustle target
    """
    lead_type = lead["lead_type"]

    if lead_type == LEAD_BAD_CATEGORY:
        return "skip"
    if lead_type in (LEAD_MANUAL_REVIEW, LEAD_MULTI_LOCATION):
        return "low"  # who/what this business is must be verified first
    if lead_type == LEAD_LOW_PRIORITY:
        return "low"
    if lead["online_presence_status"] == PRESENCE_WEAK:
        return "high"  # the ONLY path to high: hand-verified weak presence
    if is_institutional_place(lead):
        # Banks, arenas, government places, malls/plazas, commercial
        # buildings. High category confidence may mean a real business
        # operating inside one, so those stay visible at low instead.
        return "low" if lead["category_confidence"] == "high" else "skip"
    if lead["is_likely_chain"]:
        # Known chain/franchise that already has a website (chains without
        # one became MULTI_LOCATION_BRAND_REVIEW above). Website work for a
        # franchise is decided at head office — not a first-contact sale.
        return "low"
    if lead_type == LEAD_POTENTIAL_WEBSITE:
        # Missing websiteUri, presence unknown or verified-but-present.
        # Never high: unknown_not_checked must be checked before contact.
        if lead["manual_verification_priority"] == "high":
            return "medium"  # becomes high or low once manually checked
        return "low"
    # Remaining types are optimization offers on businesses that verifiably
    # exist (GBP cleanup, branch/menu/quote/appointment page, chatbot).
    return "medium"


def build_recommended_offer(lead, category):
    """Pick up to MAX_OFFERS_PER_LEAD service suggestions for this lead.

    Wording stays non-definitive until the lead's online presence has been
    manually verified — the scan alone can't prove a business has nothing.
    """
    offers = []
    lead_type = lead["lead_type"]
    review_count = lead.get("review_count") or 0

    if lead_type == LEAD_BAD_CATEGORY:
        offers.append("Probably not a business in this category — skip unless verified")
    elif lead_type == LEAD_MULTI_LOCATION:
        offers.append("Confirm brand/branches manually before pitching anything")
    elif lead_type == LEAD_MANUAL_REVIEW:
        offers.append("Verify what this business is (and its online presence) manually")
    elif lead_type == LEAD_NEW_WEBSITE:
        offers.append("One-page website + WhatsApp button")
    elif lead_type == LEAD_POTENTIAL_WEBSITE:
        offers.append(
            "Check online presence first (quick links in report); "
            "if weak: one-page website + WhatsApp button"
        )
    elif lead_type in (LEAD_GBP_CLEANUP, LEAD_BRANCH_PAGE):
        offers.append("Google Business Profile cleanup: add correct website link to this branch")
        if lead_type == LEAD_BRANCH_PAGE:
            offers.append("Branch page on the existing brand website")
    elif lead["online_presence_status"] == PRESENCE_WEBSITE and not lead["has_website"]:
        offers.append("Google Business Profile cleanup: link the website you found to the profile")

    if category:
        if category["menu_based"]:
            offers.append("Menu/catalog page or WhatsApp order flow")
        if category["quote_based"]:
            offers.append("Quote request form")
        elif category["appointment_based"]:
            offers.append("Appointment request page")
        if category["question_heavy"] and review_count >= BUSY_BUSINESS_MIN_REVIEWS:
            offers.append("FAQ chatbot")

    if not offers and category:
        offers.append(category.get("recommended_offer") or "Basic online presence check")
    if not offers:
        offers.append("Basic online presence check")
    return " + ".join(offers[:MAX_OFFERS_PER_LEAD])


def build_notes(lead, cluster, confidence_reason, manual):
    """Human-readable warnings that explain the flags on this lead."""
    notes = []
    if manual and manual.get("note"):
        notes.append(f"manual check: {manual['note']}")
    elif manual:
        notes.append(f"manually verified: {manual['online_presence']}")
    if confidence_reason:
        notes.append(confidence_reason)
    if lead["bad_category_match"]:
        notes.append("name looks like a plaza/mall/building, and types don't prove otherwise")
    elif lead["bad_category_suspect"]:
        notes.append("name looks like a plaza/mall/building — types claim the category, verify")
    if lead["possible_brand_match"]:
        notes.append("similar name to: " + "; ".join(lead["possible_brand_match"]))
    if cluster["franchise_keyword"]:
        notes.append(f"known chain/franchise ('{cluster['franchise_keyword']}')")
    elif lead["cluster_size"] >= 2 and cluster["generic_name"]:
        notes.append("generic name — same-name places may be unrelated businesses")
    elif lead["cluster_size"] >= 2:
        notes.append(f"{lead['other_locations_count']} other location(s) with the same brand")
    if lead["brand_has_website_elsewhere"] and lead["brand_website_example"]:
        notes.append(f"brand site found on another branch: {lead['brand_website_example']}")
    if not manual and lead["missing_profile_website"]:
        notes.append("no websiteUri on the Google profile — online presence not verified yet")
    return "; ".join(notes)


def main():
    parser = argparse.ArgumentParser(
        description="Score scanned businesses and attach a recommended offer."
    )
    parser.add_argument("--top", type=int, default=10, help="how many top leads to print (default 10)")
    args = parser.parse_args()

    try:
        raw_leads = load_json(SCAN_RESULTS_FILE)
    except ConfigError as exc:
        die(f"{exc}\n  Run a scan first, e.g.: py scripts/scan_places.py --all")
    if not raw_leads:
        die("Scan data is empty. Run a scan first, e.g.: py scripts/scan_places.py --all")

    try:
        categories = {cat["label"]: cat for cat in load_categories()}
        manual_checks = load_manual_checks()
    except ConfigError as exc:
        die(str(exc))

    leads, duplicates = upgrade_and_dedupe(raw_leads)
    clusters = build_clusters(leads)
    find_possible_brand_matches(leads)

    for lead in leads:
        cluster = clusters[lead["brand_key"]]
        assign_website_status(lead, cluster)

        category, matched_label, confidence, reason = pick_category(lead, categories)
        lead["matched_category"] = matched_label
        lead["category_confidence"] = confidence

        confirmed, suspect, _keyword = detect_bad_category(lead, category)
        lead["bad_category_match"] = confirmed
        lead["bad_category_suspect"] = suspect

        manual = manual_checks.get(lead["id"])
        lead["manually_verified"] = bool(manual)
        lead["online_presence_status"] = resolve_online_presence(lead, manual)

        lead["lead_type"] = pick_lead_type(lead, category)
        lead["lead_score"] = score_lead(lead, category)
        lead["manual_verification_priority"] = pick_verification_priority(lead)
        lead["sales_priority"] = pick_sales_priority(lead)
        lead["recommended_offer"] = build_recommended_offer(lead, category)
        lead["review_needed"] = (
            lead["needs_manual_review"]
            or lead["category_confidence"] == "low"
            or lead["bad_category_match"]
            or lead["bad_category_suspect"]
            or bool(lead["possible_brand_match"])
            or (lead["is_possible_chain"] and lead["missing_profile_website"])
        )
        lead["notes"] = build_notes(lead, cluster, reason, manual)

    leads.sort(key=lambda lead: lead["lead_score"], reverse=True)
    save_json(SCORED_LEADS_FILE, {"scored_at": utc_now_iso(), "leads": leads})

    chains = sum(1 for c in clusters.values() if c["size"] >= 2)
    flagged = sum(1 for lead in leads if lead["possible_brand_match"])
    verified = sum(1 for lead in leads if lead["manually_verified"])
    print(f"Scored {len(leads)} leads -> {SCORED_LEADS_FILE}")
    if duplicates:
        print(f"  merged {duplicates} duplicate row(s) with the same place id")
    print(f"  brand clusters: {len(clusters)} ({chains} with 2+ locations, "
          f"{flagged} rows with possible name matches)")
    print(f"  manually verified so far: {verified} (config/manual_checks.yml)")
    for priority in ("high", "medium", "low"):
        count = sum(1 for lead in leads if lead["manual_verification_priority"] == priority)
        print(f"  verification priority {priority} (check first): {count}")
    for priority in ("high", "medium", "low", "skip"):
        count = sum(1 for lead in leads if lead["sales_priority"] == priority)
        print(f"  sales priority {priority} (contact first): {count}")
    print()
    print(f"Top {min(args.top, len(leads))} leads:")
    print(f"  {'SCORE':>5}  {'BUSINESS':<32} {'CATEGORY':<14} {'CONF':<6} {'PRESENCE':<22} LEAD TYPE")
    for lead in leads[: args.top]:
        name = (lead.get("name") or "?")[:32]
        print(
            f"  {lead['lead_score']:>5}  {name:<32} {lead.get('matched_category', '?'):<14} "
            f"{lead['category_confidence']:<6} {lead['online_presence_status']:<22} {lead['lead_type']}"
        )
    print()
    print("Next step: py scripts/export_report.py")
    print("Then: verify top leads with the report's quick links and record what")
    print("you find in config/manual_checks.yml, re-run this script, and re-export.")


if __name__ == "__main__":
    main()
