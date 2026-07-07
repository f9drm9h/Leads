"""Score scanned businesses: cluster brands, judge data quality, pick offers.

Reads  data/scan_results.json   (produced by scan_places.py)
Writes data/leads_scored.json   (leads + brand cluster + website status +
                                 category confidence + lead type + score)

What happens to each lead:
  1. Duplicate place ids are collapsed into one row (source lists merged).
  2. A conservative brand_key groups branches of the same brand together.
  3. Website status looks across the whole brand cluster, so a branch of a
     brand that has a website elsewhere is NOT counted as a "no website" lead.
  4. Category confidence checks the Google place types (and name keywords)
     against the category that found the lead, to weed out noise like an
     auto shop showing up in a phone-repair scan.
  5. A lead type + recommended offer + score are assigned from all of that.

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
    make_brand_key,
    name_keyword_hit,
    save_json,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Scoring rules. Tweak these numbers freely — they are the whole model.
# A high score means "easy to reach, probably worth money, weak online".
# ---------------------------------------------------------------------------
POINTS_BRAND_NO_WEBSITE = 35     # every known location of this brand lacks a site
POINTS_PROFILE_NO_WEBSITE = 20   # this specific Google profile lacks a site
POINTS_HAS_PHONE = 15
POINTS_GOOD_RATING = 15          # rating >= GOOD_RATING_MIN
POINTS_MANY_REVIEWS = 15         # review_count >= MANY_REVIEWS_MIN
POINTS_HIGH_CONFIDENCE = 10      # place types clearly match the category
POINTS_SERVICE_CATEGORY = 10     # appointment-, quote- or menu-based category
POINTS_OPERATIONAL = 5
PENALTY_BRAND_HAS_WEBSITE = -30  # another location of this brand has a site
PENALTY_LOW_CONFIDENCE = -25     # probably not the category we scanned for
PENALTY_LIKELY_CHAIN = -25       # franchise / corporate branch
PENALTY_CLOSED_PERMANENTLY = -50
PENALTY_CLOSED_TEMPORARILY = -25

GOOD_RATING_MIN = 4.0
MANY_REVIEWS_MIN = 25

BUSY_BUSINESS_MIN_REVIEWS = 50       # "complex enough" for an FAQ chatbot
SIGNIFICANT_BRANCH_MIN_REVIEWS = 25  # busy branch -> worth its own branch page
LIKELY_CHAIN_MIN_LOCATIONS = 3       # 3+ scanned locations = likely a chain
MAX_OFFERS_PER_LEAD = 2              # keep the pitch simple
MAX_LISTED_BRANCH_LOCATIONS = 5      # cap the same_brand_locations list

# Website status values (see README, "Website status").
STATUS_HAS_WEBSITE = "has_website"
STATUS_BRAND_ELSEWHERE = "brand_has_website_elsewhere"
STATUS_ALL_MISSING = "all_locations_missing_website"
STATUS_NEEDS_REVIEW = "needs_manual_review"

# Lead types, roughly from most to least actionable.
LEAD_NEW_WEBSITE = "NEW_WEBSITE_LEAD"
LEAD_GBP_CLEANUP = "GBP_CLEANUP_LEAD"
LEAD_BRANCH_PAGE = "BRANCH_PAGE_LEAD"
LEAD_MENU_PAGE = "MENU_PAGE_LEAD"
LEAD_QUOTE_FORM = "QUOTE_FORM_LEAD"
LEAD_APPOINTMENT = "APPOINTMENT_PAGE_LEAD"
LEAD_CHATBOT = "CHATBOT_CANDIDATE"
LEAD_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
LEAD_LOW_PRIORITY = "LOW_PRIORITY"

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
        lead["is_possible_chain"] = cluster["size"] >= 2 or bool(cluster["franchise_keyword"])
        lead["is_likely_chain"] = cluster["likely_chain"]
        lead["other_locations_count"] = cluster["size"] - 1
        lead["same_brand_locations"] = listed
        lead["cluster_uncertain"] = cluster["uncertain"]
    return clusters


def assign_website_status(lead, cluster):
    """Set the five website flags plus the single website_status value."""
    has_site = bool(lead.get("website"))
    lead["has_website"] = has_site
    lead["missing_profile_website"] = not has_site
    lead["brand_has_website_elsewhere"] = False
    lead["all_locations_missing_website"] = False
    lead["needs_manual_review"] = False
    lead["brand_website_example"] = ""

    if has_site:
        lead["website_status"] = STATUS_HAS_WEBSITE
    elif cluster["uncertain"]:
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


def category_confidence(lead, category):
    """How sure are we this lead really belongs to the scanned category?

    Returns (level, reason): level is 'high', 'medium' or 'low'.
    High requires the Google place types to match the category; excluded
    types/keywords push a lead down unless the types clearly support it.
    """
    types = set(lead.get("types") or [])
    if lead.get("primary_type"):
        types.add(lead["primary_type"])

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


def pick_lead_type(lead, category):
    """Classify what kind of opportunity this lead is."""
    status = lead.get("business_status", "")
    if status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return LEAD_LOW_PRIORITY
    if lead["website_status"] == STATUS_NEEDS_REVIEW or lead["category_confidence"] == "low":
        return LEAD_MANUAL_REVIEW
    if lead["all_locations_missing_website"]:
        return LEAD_NEW_WEBSITE
    if lead["brand_has_website_elsewhere"]:
        if (lead.get("review_count") or 0) >= SIGNIFICANT_BRANCH_MIN_REVIEWS:
            return LEAD_BRANCH_PAGE
        return LEAD_GBP_CLEANUP
    # From here on the profile has a website.
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

    if lead["all_locations_missing_website"]:
        score += POINTS_BRAND_NO_WEBSITE
    if lead["missing_profile_website"]:
        score += POINTS_PROFILE_NO_WEBSITE
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
    if lead["is_likely_chain"]:
        score += PENALTY_LIKELY_CHAIN
    return score


def build_recommended_offer(lead, category):
    """Pick up to MAX_OFFERS_PER_LEAD service suggestions for this lead."""
    offers = []
    status = lead["website_status"]
    review_count = lead.get("review_count") or 0

    if status == STATUS_NEEDS_REVIEW:
        offers.append("Verify brand and branches manually before pitching")
    elif status == STATUS_ALL_MISSING:
        offers.append("One-page website + WhatsApp button")
    elif status == STATUS_BRAND_ELSEWHERE:
        offers.append("Google Business Profile cleanup: add correct website link to this branch")
        if lead["lead_type"] == LEAD_BRANCH_PAGE:
            offers.append("Branch page on the existing brand website")

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


def build_notes(lead, cluster, confidence_reason):
    """Human-readable warnings that explain the flags on this lead."""
    notes = []
    if confidence_reason:
        notes.append(confidence_reason)
    if cluster["franchise_keyword"]:
        notes.append(f"known chain/franchise ('{cluster['franchise_keyword']}')")
    elif lead["cluster_size"] >= 2 and lead["cluster_uncertain"]:
        notes.append("generic name — same-name places may be unrelated businesses")
    elif lead["cluster_size"] >= 2:
        notes.append(f"{lead['other_locations_count']} other location(s) with the same brand")
    if lead["brand_has_website_elsewhere"] and lead["brand_website_example"]:
        notes.append(f"brand site found on another branch: {lead['brand_website_example']}")
    if lead["missing_profile_website"] and lead["website_status"] != STATUS_ALL_MISSING:
        notes.append("missing website on THIS profile only — verify before pitching a new site")
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
    except ConfigError as exc:
        die(str(exc))

    leads, duplicates = upgrade_and_dedupe(raw_leads)
    clusters = build_clusters(leads)

    for lead in leads:
        cluster = clusters[lead["brand_key"]]
        assign_website_status(lead, cluster)

        category, matched_label, confidence, reason = pick_category(lead, categories)
        lead["matched_category"] = matched_label
        lead["category_confidence"] = confidence

        lead["lead_type"] = pick_lead_type(lead, category)
        lead["lead_score"] = score_lead(lead, category)
        lead["recommended_offer"] = build_recommended_offer(lead, category)
        lead["review_needed"] = (
            lead["needs_manual_review"]
            or lead["category_confidence"] == "low"
            or (lead["is_possible_chain"] and lead["missing_profile_website"])
        )
        lead["notes"] = build_notes(lead, cluster, reason)

    leads.sort(key=lambda lead: lead["lead_score"], reverse=True)
    save_json(SCORED_LEADS_FILE, {"scored_at": utc_now_iso(), "leads": leads})

    chains = sum(1 for c in clusters.values() if c["size"] >= 2)
    print(f"Scored {len(leads)} leads -> {SCORED_LEADS_FILE}")
    if duplicates:
        print(f"  merged {duplicates} duplicate row(s) with the same place id")
    print(f"  brand clusters: {len(clusters)} ({chains} with 2+ locations)")
    for status in (STATUS_ALL_MISSING, STATUS_BRAND_ELSEWHERE, STATUS_NEEDS_REVIEW, STATUS_HAS_WEBSITE):
        count = sum(1 for lead in leads if lead["website_status"] == status)
        print(f"  {status}: {count}")
    print()
    print(f"Top {min(args.top, len(leads))} leads:")
    print(f"  {'SCORE':>5}  {'BUSINESS':<32} {'CATEGORY':<14} {'CONF':<6} {'WEBSITE STATUS':<28} LEAD TYPE")
    for lead in leads[: args.top]:
        name = (lead.get("name") or "?")[:32]
        print(
            f"  {lead['lead_score']:>5}  {name:<32} {lead.get('matched_category', '?'):<14} "
            f"{lead['category_confidence']:<6} {lead['website_status']:<28} {lead['lead_type']}"
        )
    print()
    print("Next step: py scripts/export_report.py")


if __name__ == "__main__":
    main()
