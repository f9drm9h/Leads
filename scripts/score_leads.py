"""Score scanned businesses and pick a recommended service offer for each.

Reads  data/scan_results.json   (produced by scan_places.py)
Writes data/leads_scored.json   (same leads + lead_score + recommended_offer)

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
    load_categories,
    load_json,
    save_json,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Scoring rules. Tweak these numbers freely — they are the whole model.
# A high score means "easy to reach, probably worth money, weak online".
# ---------------------------------------------------------------------------
POINTS_NO_WEBSITE = 35        # the core signal: no website = our best prospect
POINTS_HAS_PHONE = 20         # we can actually contact them
POINTS_GOOD_RATING = 15       # rating >= GOOD_RATING_MIN
POINTS_MANY_REVIEWS = 15      # review_count >= MANY_REVIEWS_MIN
POINTS_SERVICE_CATEGORY = 15  # appointment-, quote- or menu-based category
POINTS_HAS_MAPS_URL = 10      # existing Google profile we can reference
POINTS_OPERATIONAL = 5
PENALTY_CLOSED_PERMANENTLY = -50
PENALTY_CLOSED_TEMPORARILY = -25
PENALTY_NO_PHONE = -10
PENALTY_LOW_RATING = -10      # rating < LOW_RATING_MAX with enough reviews

GOOD_RATING_MIN = 4.0
MANY_REVIEWS_MIN = 25
LOW_RATING_MAX = 3.5
LOW_RATING_MIN_REVIEWS = 10

# Thresholds used by the recommended-offer rules below.
WEAK_PROFILE_MAX_REVIEWS = 10  # fewer reviews than this = profile needs love
BUSY_BUSINESS_MIN_REVIEWS = 50  # busy enough that an FAQ chatbot saves time

MAX_OFFERS_PER_LEAD = 2  # keep the pitch simple: at most two suggestions


def score_lead(lead, category):
    """Apply the scoring formula to one lead. Returns an integer score."""
    score = 0
    rating = lead.get("rating")
    review_count = lead.get("review_count") or 0
    status = lead.get("business_status", "")

    if not lead.get("website"):
        score += POINTS_NO_WEBSITE
    if lead.get("phone"):
        score += POINTS_HAS_PHONE
    else:
        score += PENALTY_NO_PHONE
    if rating is not None and rating >= GOOD_RATING_MIN:
        score += POINTS_GOOD_RATING
    if review_count >= MANY_REVIEWS_MIN:
        score += POINTS_MANY_REVIEWS
    if category and (
        category["appointment_based"] or category["quote_based"] or category["menu_based"]
    ):
        score += POINTS_SERVICE_CATEGORY
    if lead.get("google_maps_url"):
        score += POINTS_HAS_MAPS_URL
    if status == "OPERATIONAL":
        score += POINTS_OPERATIONAL
    elif status == "CLOSED_PERMANENTLY":
        score += PENALTY_CLOSED_PERMANENTLY
    elif status == "CLOSED_TEMPORARILY":
        score += PENALTY_CLOSED_TEMPORARILY
    if rating is not None and rating < LOW_RATING_MAX and review_count >= LOW_RATING_MIN_REVIEWS:
        score += PENALTY_LOW_RATING

    return score


def build_recommended_offer(lead, category):
    """Pick up to MAX_OFFERS_PER_LEAD service suggestions for this lead.

    Rules, in priority order:
      1. No website            -> one-page website with WhatsApp button
      2. Category is menu-based        -> menu/catalog page
         Category is appointment-based -> appointment booking page
         Category is quote-based       -> quote request form
      3. Weak Google profile (no phone, no Maps link, or very few reviews)
                               -> Google Business Profile cleanup
      4. Has a website and is busy     -> FAQ chatbot
      5. Nothing matched       -> the category's recommended_offer from config
    """
    offers = []

    if not lead.get("website"):
        offers.append("One-page website with WhatsApp button")

    if category:
        if category["menu_based"]:
            offers.append("Menu/catalog page")
        if category["appointment_based"]:
            offers.append("Appointment booking page")
        if category["quote_based"]:
            offers.append("Quote request form")

    weak_profile = (
        not lead.get("phone")
        or not lead.get("google_maps_url")
        or (lead.get("review_count") or 0) < WEAK_PROFILE_MAX_REVIEWS
    )
    if weak_profile:
        offers.append("Google Business Profile cleanup")

    if lead.get("website") and (lead.get("review_count") or 0) >= BUSY_BUSINESS_MIN_REVIEWS:
        offers.append("FAQ chatbot")

    if not offers and category:
        offers.append(category.get("recommended_offer") or "Basic online presence check")

    return " + ".join(offers[:MAX_OFFERS_PER_LEAD])


def main():
    parser = argparse.ArgumentParser(
        description="Score scanned businesses and attach a recommended offer."
    )
    parser.add_argument("--top", type=int, default=10, help="how many top leads to print (default 10)")
    args = parser.parse_args()

    try:
        leads = load_json(SCAN_RESULTS_FILE)
    except ConfigError as exc:
        die(f"{exc}\n  Run a scan first, e.g.: py scripts/scan_places.py --all")
    if not leads:
        die("Scan data is empty. Run a scan first, e.g.: py scripts/scan_places.py --all")

    try:
        categories = {cat["label"]: cat for cat in load_categories()}
    except ConfigError as exc:
        die(str(exc))

    for lead in leads:
        # If a category was renamed in the config since the scan, we still
        # score the lead — it just loses the category-specific points/offers.
        category = categories.get(lead.get("source_category"))
        lead["lead_score"] = score_lead(lead, category)
        lead["recommended_offer"] = build_recommended_offer(lead, category)

    leads.sort(key=lambda lead: lead["lead_score"], reverse=True)
    save_json(SCORED_LEADS_FILE, {"scored_at": utc_now_iso(), "leads": leads})

    print(f"Scored {len(leads)} leads -> {SCORED_LEADS_FILE}")
    print()
    print(f"Top {min(args.top, len(leads))} leads:")
    print(f"  {'SCORE':>5}  {'BUSINESS':<38} {'CATEGORY':<15} WEBSITE")
    for lead in leads[: args.top]:
        website = "has site" if lead.get("website") else "NO SITE"
        name = (lead.get("name") or "?")[:38]
        print(f"  {lead['lead_score']:>5}  {name:<38} {lead.get('source_category', '?'):<15} {website}")
    print()
    print("Next step: py scripts/export_report.py")


if __name__ == "__main__":
    main()
