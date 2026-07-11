"""Export scored leads into PUBLIC and PRIVATE reports.

Reads  data/leads_scored.json  (produced by score_leads.py)

PUBLIC output  -> reports/   (committed; served by GitHub Pages — the repo
                              and Pages site are public!)
    leads.html, leads_mobile.html — a neutral business DIRECTORY only:
    name, category, neighborhood, address, publicly listed phone/website,
    rating and a Google Maps link. No scores, no sales/verification
    priorities, no presence analysis, no recommended pitches, no notes.

PRIVATE output -> private/   (gitignored; NEVER commit or publish)
    leads.html, leads_mobile.html, leads.csv, leads.json — the full sales
    research view: scores, lead types, both priorities, presence analysis,
    recommended offers, verification notes and shortlist ordering.

Usage:
    py scripts/export_report.py
    py scripts/export_report.py --min-score 50    (private reports only show
                                                   stronger leads; the public
                                                   directory is unaffected)

Private CSV/JSON are sorted by score. The private HTML report is also
score-ordered, except that branches of the same brand cluster are pulled
together under the cluster's best-scoring member so possible chains are easy
to spot. The public directory is sorted by category, then name — it carries
no ranking information at all.
"""

import argparse
import csv
import html
from string import Template
from urllib.parse import quote_plus

from common import (
    PRIVATE_DIR,
    REPORTS_DIR,
    SCORED_LEADS_FILE,
    ConfigError,
    die,
    load_json,
    load_scan_areas,
    save_json,
    utc_now_iso,
)

CSV_COLUMNS = [
    "lead_score",
    "lead_type",
    "manual_verification_priority",
    "sales_priority",
    "name",
    "matched_category",
    "category_confidence",
    "online_presence_status",
    "manually_verified",
    "website_status",
    "brand_key",
    "core_brand_key",
    "cluster_id",
    "cluster_size",
    "is_possible_chain",
    "other_locations_count",
    "possible_brand_match",
    "bad_category_match",
    "has_website",
    "missing_profile_website",
    "brand_has_website_elsewhere",
    "all_locations_missing_website",
    "needs_manual_review",
    "review_needed",
    "phone",
    "website",
    "brand_website_example",
    "rating",
    "review_count",
    "business_status",
    "recommended_offer",
    "notes",
    "same_brand_locations",
    "address",
    "google_maps_url",
    "primary_type",
    "types",
    "source_categories",
    "source_areas",
    "scanned_at",
    "id",
]

LIST_COLUMNS = {
    "same_brand_locations",
    "possible_brand_match",
    "types",
    "source_categories",
    "source_areas",
}

# The ONLY lead fields that may appear in the public directory pages.
# Everything else (scores, priorities, lead types, offers, notes, cluster
# and presence analysis...) is private sales research and stays in private/.
PUBLIC_FIELDS = (
    "name",
    "matched_category",
    "source_category",
    "source_areas",
    "address",
    "phone",
    "website",
    "google_maps_url",
    "rating",
    "review_count",
    "business_status",
)

# Labels describe the GOOGLE PROFILE only — a missing websiteUri never means
# the business has no online presence (Instagram, Fresha, Facebook, ...).
WEBSITE_STATUS_LABELS = {
    "has_website": "Website on Google profile",
    "brand_has_website_elsewhere": "Brand site on another branch",
    "all_locations_missing_website": "No website on Google profile",
    "needs_manual_review": "Needs manual online-presence check",
}

ONLINE_PRESENCE_LABELS = {
    "unknown_not_checked": "Unknown — not checked",
    "weak_or_missing": "Weak/missing (verified)",
    "has_social_presence": "Social presence (verified)",
    "has_booking_presence": "Booking presence (verified)",
    "has_directory_presence": "Directory presence (verified)",
    "has_website": "Has website",
    "needs_manual_review": "Needs manual check",
}

# Shared filter widget: a neighborhood dropdown and a category dropdown that
# together hide every element whose data-areas / data-cats attributes do not
# contain the selected values. Both filters apply at the same time.
FILTER_CSS = """\
  .filterbar { margin: 0 0 14px; display: flex; align-items: center;
               gap: 8px; flex-wrap: wrap; font-size: 13px; }
  .filterbar select { font-size: 14px; padding: 6px 8px; border-radius: 8px;
                      border: 1px solid #c7d0d9; background: #fff; max-width: 100%; }
  .filterbar .count { color: #57606a; }
"""

FILTER_SCRIPT = """\
<script>
(function () {
  var areaSelect = document.getElementById("area-filter");
  var catSelect = document.getElementById("category-filter");
  if (!areaSelect && !catSelect) return;
  var counter = document.getElementById("area-filter-count");
  function matches(item, select, attr) {
    if (!select || !select.value) return true;
    var values = (item.getAttribute(attr) || "").split("|");
    return values.indexOf(select.value) !== -1;
  }
  function apply() {
    var shown = 0;
    var items = document.querySelectorAll("[data-areas], [data-cats]");
    for (var i = 0; i < items.length; i++) {
      var show = matches(items[i], areaSelect, "data-areas") &&
                 matches(items[i], catSelect, "data-cats");
      items[i].style.display = show ? "" : "none";
      if (show) shown++;
    }
    if (counter) counter.textContent = shown + " shown";
  }
  if (areaSelect) areaSelect.addEventListener("change", apply);
  if (catSelect) catSelect.addEventListener("change", apply);
  apply();
})();
</script>
"""


HTML_PAGE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local business leads (private)</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         margin: 24px; color: #1c2733; background: #f6f8fa; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: #57606a; margin: 0 0 10px; font-size: 13px; }
  .private-banner { background: #ffe5e0; color: #a12318; font-weight: 600;
                    padding: 8px 12px; border-radius: 8px; font-size: 13px;
                    margin: 0 0 12px; }
  .legend { color: #57606a; font-size: 12px; margin: 0 0 18px; line-height: 1.7; }
  .legend b { color: #1c2733; }
$filter_css
  .tablewrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); font-size: 13px; }
  th, td { padding: 7px 9px; border-bottom: 1px solid #e2e8f0;
           text-align: left; vertical-align: top; }
  th { background: #eef2f6; position: sticky; top: 0; white-space: nowrap; }
  tr:hover td { background: #f0f6ff; }
  tr.chain td { background: #fbf6ea; }
  tr.chain:hover td { background: #f5eeda; }
  tr.chain td:first-child { border-left: 3px solid #b58a00; }
  .score { display: inline-block; min-width: 34px; text-align: center;
           padding: 2px 8px; border-radius: 12px; font-weight: 600; color: #fff; }
  .score-high { background: #1a7f37; }
  .score-mid  { background: #b58a00; }
  .score-low  { background: #8c959f; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 10px;
           font-size: 11px; font-weight: 600; white-space: nowrap; }
  .ws-missing { background: #ffe5e0; color: #a12318; }
  .ws-elsewhere { background: #fff2cc; color: #7a5b00; }
  .ws-review { background: #ece3fa; color: #5a2ca0; }
  .ws-has { background: #dcf2e2; color: #14602a; }
  .conf-high { background: #dcf2e2; color: #14602a; }
  .conf-medium { background: #fff2cc; color: #7a5b00; }
  .conf-low { background: #ffe5e0; color: #a12318; }
  .leadtype { font-size: 11px; font-weight: 600; color: #354150; white-space: nowrap; }
  .op-unknown { background: #e8ebef; color: #57606a; }
  .op-weak { background: #ffe5e0; color: #a12318; }
  .op-has { background: #dcf2e2; color: #14602a; }
  .op-review { background: #ece3fa; color: #5a2ca0; }
  .prio-high { background: #a12318; color: #fff; }
  .prio-medium { background: #b58a00; color: #fff; }
  .prio-low { background: #8c959f; color: #fff; }
  .prio-skip { background: #e8ebef; color: #57606a; }
  .qlinks { white-space: nowrap; font-size: 12px; }
  .qlinks a { margin-right: 6px; }
  .matches { font-size: 12px; color: #7a5b00; max-width: 220px; }
  .closed { color: #a12318; font-size: 12px; font-weight: 600; }
  .cluster { font-size: 12px; }
  .chainbadge { color: #7a5b00; font-weight: 600; }
  .missing { color: #c0392b; font-weight: 600; }
  .review-yes { color: #a12318; font-weight: 700; }
  .addr { color: #57606a; font-size: 12px; }
  .notes { color: #8a6d1a; font-size: 12px; font-style: italic; max-width: 340px; }
  .muted { color: #8c959f; }
  .business-link { color: #0969da; font-weight: 700; text-decoration: none; }
  .business-link:hover, .business-link:focus { text-decoration: underline; }
</style>
</head>
<body>
<h1>Local business leads</h1>
<p class="private-banner">PRIVATE sales research — this file lives in the
gitignored <code>private/</code> folder. Do not commit, publish or share it.</p>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; <b>$lead_count leads</b> &nbsp;&middot;&nbsp;
$multi_group_count possible multi-location brand group(s) &nbsp;&middot;&nbsp;
$review_rows row(s) needing manual review &nbsp;&middot;&nbsp;
$potential_count potential website lead(s)$verified_weak_html &nbsp;&middot;&nbsp;
$unique_brand_keys unique brand keys (name-grouping aid &mdash; most cover a single business) &nbsp;&middot;&nbsp;
short-term research snapshot from the official Google Places API &mdash; re-scan before outreach.</p>
<p class="legend">
<b>This is a prioritization aid, not proof.</b> "No website on Google profile" only means
the Places API returned no <code>websiteUri</code> for that profile &mdash; the business may still
have Instagram, Facebook, Fresha/Booksy, a directory page or an unlinked website.
Use the <b>quick search links</b> to verify by hand, record what you find in
<code>config/manual_checks.local.yml</code>, then re-run the score + export steps.
Only manually verified leads are ever labeled definitively.<br>
<b>Google profile website:</b>
<span class="badge ws-missing">No website on Google profile</span> not returned by the Places API for any scanned location of this brand &nbsp;&middot;&nbsp;
<span class="badge ws-elsewhere">Brand site on another branch</span> fix this profile's link, don't sell a new site &nbsp;&middot;&nbsp;
<span class="badge ws-review">Needs manual online-presence check</span> uncertain brand grouping, known chain, or a likely name match &nbsp;&middot;&nbsp;
<span class="badge ws-has">Website on Google profile</span>.<br>
<b>Verify priority</b> = <i>check this lead first</i>: how urgently a human should confirm the
business's real online presence (use the quick links, record the result in
<code>config/manual_checks.local.yml</code>). &nbsp;
<b>Sales priority</b> = <i>contact this lead first</i>: it only becomes <b>high</b> after a manual
check confirms weak/missing presence, so a promising-but-unchecked lead is Verify:&nbsp;high /
Sales:&nbsp;medium &mdash; it may have strong Instagram, booking or brand presence the API can't see.
<b>Do not contact high-verify leads until their online presence has been manually checked.</b><br>
Rows with a yellow tint belong to a possible multi-location brand &mdash; branches are grouped
under their best-scoring location but every branch is still listed. Verify every
"Verify: high" row before outreach.
</p>
$area_filter
<div class="tablewrap">
<table>
  <thead>
    <tr>
      <th>Score</th><th>Business</th><th>Category</th><th>Conf.</th>
      <th>Brand cluster</th><th>Possible brand matches</th>
      <th>Google profile website</th><th>Online presence</th><th>Lead type</th>
      <th>Verify priority</th><th>Sales priority</th><th>Quick search</th>
      <th>Phone</th><th>Rating</th><th>Maps</th>
      <th>Recommended offer</th><th>Review?</th>
    </tr>
  </thead>
  <tbody>
$rows
  </tbody>
</table>
</div>
$filter_script
</body>
</html>
""")


MOBILE_PAGE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local business leads (private, mobile)</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         margin: 12px; color: #1c2733; background: #f6f8fa; }
  h1 { margin: 0 0 4px; font-size: 20px; }
  .meta { color: #57606a; margin: 0 0 8px; font-size: 12px; }
  .private-banner { background: #ffe5e0; color: #a12318; font-weight: 600;
                    padding: 8px 12px; border-radius: 8px; font-size: 12px;
                    margin: 0 0 10px; }
  .legend { color: #57606a; font-size: 12px; margin: 0 0 14px; line-height: 1.6; }
  .legend b { color: #1c2733; }
$filter_css
  .card { background: #fff; border-radius: 10px; padding: 12px;
          margin: 0 0 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card.chain { background: #fbf6ea; border-left: 4px solid #b58a00; }
  .cardtop { display: flex; align-items: baseline; gap: 8px; }
  .cardname { font-size: 15px; font-weight: 700; }
  .business-link { color: #0969da; font-weight: 700; text-decoration: none; }
  .business-link:hover, .business-link:focus { text-decoration: underline; }
  .score { display: inline-block; min-width: 34px; text-align: center;
           padding: 2px 8px; border-radius: 12px; font-weight: 600;
           color: #fff; flex-shrink: 0; }
  .score-high { background: #1a7f37; }
  .score-mid  { background: #b58a00; }
  .score-low  { background: #8c959f; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 10px;
           font-size: 11px; font-weight: 600; white-space: nowrap; }
  .ws-missing { background: #ffe5e0; color: #a12318; }
  .ws-elsewhere { background: #fff2cc; color: #7a5b00; }
  .ws-review { background: #ece3fa; color: #5a2ca0; }
  .ws-has { background: #dcf2e2; color: #14602a; }
  .op-unknown { background: #e8ebef; color: #57606a; }
  .op-weak { background: #ffe5e0; color: #a12318; }
  .op-has { background: #dcf2e2; color: #14602a; }
  .op-review { background: #ece3fa; color: #5a2ca0; }
  .prio-high { background: #a12318; color: #fff; }
  .prio-medium { background: #b58a00; color: #fff; }
  .prio-low { background: #8c959f; color: #fff; }
  .prio-skip { background: #e8ebef; color: #57606a; }
  .review-yes { color: #a12318; font-weight: 700; font-size: 11px; }
  .chainbadge { color: #7a5b00; font-weight: 600; font-size: 11px; }
  .addr { color: #57606a; font-size: 12px; margin-top: 2px; }
  .notes { color: #8a6d1a; font-size: 12px; font-style: italic; margin-top: 2px; }
  .badges { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
  .row2 { font-size: 12px; color: #354150; margin-top: 6px; }
  .btnrow { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .btn { display: inline-block; padding: 7px 14px; border-radius: 8px;
         background: #eef2f6; color: #1c2733; font-size: 13px; font-weight: 600;
         text-decoration: none; }
  .btn-maps { background: #0969da; color: #fff; }
  .closed { color: #a12318; font-size: 12px; font-weight: 600; margin-top: 2px; }
  .missing { color: #c0392b; font-weight: 600; }
  .muted { color: #8c959f; }
</style>
</head>
<body>
<h1>Local business leads</h1>
<p class="private-banner">PRIVATE sales research — lives in the gitignored
<code>private/</code> folder. Do not commit, publish or share it.</p>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; <b>$lead_count leads</b></p>
<p class="legend">
<b>This is a prioritization aid, not proof.</b> Tap a business name to open its
Google Maps profile. Use the search buttons to verify a lead by hand, record what
you find in <code>config/manual_checks.local.yml</code>, then re-run the score + export
steps. <b>Do not contact "Verify: high" leads until their online presence has been
manually checked.</b> The full column view lives in <code>private/leads.html</code>.
</p>
$area_filter
$cards
$filter_script
</body>
</html>
""")


PUBLIC_PAGE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local business directory</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         margin: 24px; color: #1c2733; background: #f6f8fa; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: #57606a; margin: 0 0 10px; font-size: 13px; }
  .legend { color: #57606a; font-size: 12px; margin: 0 0 18px; line-height: 1.7; }
$filter_css
  .tablewrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); font-size: 13px; }
  th, td { padding: 7px 9px; border-bottom: 1px solid #e2e8f0;
           text-align: left; vertical-align: top; }
  th { background: #eef2f6; position: sticky; top: 0; white-space: nowrap; }
  tr:hover td { background: #f0f6ff; }
  .addr { color: #57606a; font-size: 12px; }
  .closed { color: #a12318; font-size: 12px; font-weight: 600; }
  .muted { color: #8c959f; }
  .business-link { color: #0969da; font-weight: 700; text-decoration: none; }
  .business-link:hover, .business-link:focus { text-decoration: underline; }
</style>
</head>
<body>
<h1>Local business directory</h1>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; <b>$lead_count businesses</b></p>
<p class="legend">A snapshot of publicly listed business profiles in Santo
Domingo / Santo Domingo Este, from the official Google Places API. Shown:
name, category, neighborhood, address, and the phone, website and rating
listed on each public Google profile. Data may be out of date &mdash; always
confirm details directly with the business. Business names open the Google
Maps profile.</p>
$area_filter
<div class="tablewrap">
<table>
  <thead>
    <tr>
      <th>Business</th><th>Category</th><th>Neighborhood</th>
      <th>Phone</th><th>Website</th><th>Rating</th><th>Maps</th>
    </tr>
  </thead>
  <tbody>
$rows
  </tbody>
</table>
</div>
$filter_script
</body>
</html>
""")


PUBLIC_MOBILE_PAGE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local business directory (mobile)</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         margin: 12px; color: #1c2733; background: #f6f8fa; }
  h1 { margin: 0 0 4px; font-size: 20px; }
  .meta { color: #57606a; margin: 0 0 8px; font-size: 12px; }
  .legend { color: #57606a; font-size: 12px; margin: 0 0 14px; line-height: 1.6; }
$filter_css
  .card { background: #fff; border-radius: 10px; padding: 12px;
          margin: 0 0 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .cardname { font-size: 15px; font-weight: 700; }
  .business-link { color: #0969da; font-weight: 700; text-decoration: none; }
  .business-link:hover, .business-link:focus { text-decoration: underline; }
  .addr { color: #57606a; font-size: 12px; margin-top: 2px; }
  .row2 { font-size: 12px; color: #354150; margin-top: 6px; }
  .btnrow { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .btn { display: inline-block; padding: 7px 14px; border-radius: 8px;
         background: #eef2f6; color: #1c2733; font-size: 13px; font-weight: 600;
         text-decoration: none; }
  .btn-maps { background: #0969da; color: #fff; }
  .closed { color: #a12318; font-size: 12px; font-weight: 600; margin-top: 2px; }
  .missing { color: #c0392b; font-weight: 600; }
  .muted { color: #8c959f; }
</style>
</head>
<body>
<h1>Local business directory</h1>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; <b>$lead_count businesses</b></p>
<p class="legend">Publicly listed business profiles in Santo Domingo /
Santo Domingo Este (official Google Places API). Tap a business name for its
Google Maps profile. Data may be out of date &mdash; confirm with the
business.</p>
$area_filter
$cards
$filter_script
</body>
</html>
""")


def score_css_class(score):
    """Color bucket for the score badge in the HTML report."""
    if score >= 70:
        return "score-high"
    if score >= 40:
        return "score-mid"
    return "score-low"


def area_labels(lead):
    """All scan-area labels that ever found this lead."""
    labels = lead.get("source_areas") or []
    if not labels and lead.get("source_area"):
        labels = [lead["source_area"]]
    return [str(label) for label in labels]


def area_display(lead, area_names):
    """Human-readable neighborhood names for a lead's scan areas."""
    return "; ".join(area_names.get(label, label) for label in area_labels(lead))


def lead_category_label(lead):
    """The category label a lead is displayed under (matched, else source)."""
    return lead.get("matched_category") or lead.get("source_category") or ""


def data_areas_attr(lead):
    """data-areas + data-cats attributes used by the filter widget."""
    parts = []
    labels = area_labels(lead)
    if labels:
        parts.append(f' data-areas="{html.escape("|".join(labels), quote=True)}"')
    category = lead_category_label(lead)
    if category:
        parts.append(f' data-cats="{html.escape(category, quote=True)}"')
    return "".join(parts)


def build_area_filter(leads, area_names):
    """Neighborhood + category <select>s for the filter widget."""
    seen = []
    for lead in leads:
        for label in area_labels(lead):
            if label not in seen:
                seen.append(label)
    categories = sorted({lead_category_label(lead) for lead in leads} - {""})

    selects = []
    if seen:
        options = ['<option value="">All neighborhoods</option>']
        for label in sorted(seen, key=lambda item: area_names.get(item, item).lower()):
            display = area_names.get(label, label)
            options.append(
                f'<option value="{html.escape(label, quote=True)}">{html.escape(display)}</option>'
            )
        selects.append(
            '<label for="area-filter"><b>Neighborhood:</b></label> '
            f'<select id="area-filter">{"".join(options)}</select>'
        )
    if categories:
        options = ['<option value="">All categories</option>']
        for label in categories:
            options.append(
                f'<option value="{html.escape(label, quote=True)}">{html.escape(label)}</option>'
            )
        selects.append(
            '<label for="category-filter"><b>Category:</b></label> '
            f'<select id="category-filter">{"".join(options)}</select>'
        )
    if not selects:
        return ""
    return (
        '<div class="filterbar">' + " ".join(selects) +
        ' <span class="count" id="area-filter-count"></span></div>'
    )


def business_name_html(lead):
    """Business name linked to its Google Maps profile (plain text if none)."""
    esc = html.escape
    name = esc(lead.get("name", ""))
    maps_url = lead.get("google_maps_url") or ""
    if not maps_url:
        return name
    return (
        f'<a href="{esc(maps_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer" class="business-link">{name}</a>'
    )


def website_status_badge(lead):
    """Website-status badge (the mobile card uses it without the site link)."""
    status = lead.get("website_status", "")
    label = WEBSITE_STATUS_LABELS.get(status, status or "?")
    css = {
        "has_website": "ws-has",
        "brand_has_website_elsewhere": "ws-elsewhere",
        "all_locations_missing_website": "ws-missing",
        "needs_manual_review": "ws-review",
    }.get(status, "ws-review")
    return f'<span class="badge {css}">{html.escape(label)}</span>'


def website_cell(lead):
    """Status badge (+ the most useful link we have) for the website column."""
    esc = html.escape
    parts = [website_status_badge(lead)]

    link = lead.get("website") or lead.get("brand_website_example") or ""
    if link:
        text = "site" if lead.get("website") else "brand site"
        parts.append(f'<a href="{esc(link, quote=True)}" target="_blank">{text}</a>')
    return "<br>".join(parts)


def presence_cell(lead):
    """Badge for online_presence_status (per manual verification, if any)."""
    status = lead.get("online_presence_status", "unknown_not_checked")
    label = ONLINE_PRESENCE_LABELS.get(status, status)
    css = {
        "unknown_not_checked": "op-unknown",
        "weak_or_missing": "op-weak",
        "has_social_presence": "op-has",
        "has_booking_presence": "op-has",
        "has_directory_presence": "op-has",
        "has_website": "op-has",
        "needs_manual_review": "op-review",
    }.get(status, "op-unknown")
    return f'<span class="badge {css}">{html.escape(label)}</span>'


def priority_badge(priority, label=""):
    """Badge for a priority value (verify and sales columns share the look)."""
    return (
        f'<span class="badge prio-{html.escape(priority)}">'
        f"{html.escape(label + priority)}</span>"
    )


def matches_cell(lead):
    """Possible same-brand rows (flagged by core-name match, never merged)."""
    matches = lead.get("possible_brand_match") or []
    if not matches:
        return '<span class="muted">&mdash;</span>'
    listed = "<br>".join(html.escape(name) for name in matches)
    return f'<span class="matches">{listed}</span>'


def city_from_address(address):
    """Best-effort city for search queries: the part before the country."""
    parts = [part.strip() for part in str(address or "").split(",") if part.strip()]
    return parts[-2] if len(parts) >= 2 else ""


def quick_search_queries(lead):
    """Manual-verification search queries (no scraping — just search URLs)."""
    name = lead.get("name", "")
    if not name:
        return {}
    city = city_from_address(lead.get("address", ""))
    return {
        "google": quote_plus(f'"{name}" {city}'.strip()),
        "instagram": quote_plus(f'"{name}" site:instagram.com'),
        "facebook": quote_plus(f'"{name}" site:facebook.com'),
    }


def quick_links_cell(lead):
    """Manual-verification search links (no scraping — just search URLs)."""
    queries = quick_search_queries(lead)
    if not queries:
        return '<span class="muted">&mdash;</span>'
    esc = html.escape
    return (
        '<span class="qlinks">'
        f'<a href="https://www.google.com/search?q={esc(queries["google"], quote=True)}" target="_blank">Google</a>'
        f'<a href="https://www.google.com/search?q={esc(queries["instagram"], quote=True)}" target="_blank">IG</a>'
        f'<a href="https://www.google.com/search?q={esc(queries["facebook"], quote=True)}" target="_blank">FB</a>'
        "</span>"
    )


def cluster_cell(lead):
    """Brand cluster column: only noisy when there is something to say."""
    esc = html.escape
    size = lead.get("cluster_size", 1)
    if size <= 1 and not lead.get("is_possible_chain"):
        return '<span class="muted">&mdash;</span>'
    badge = f'<span class="chainbadge">&#9939; {size} location(s)</span>'
    key = esc(lead.get("brand_key", ""))
    cid = esc(lead.get("cluster_id", ""))
    return f'<span class="cluster">{badge}<br>{key} <span class="muted">({cid})</span></span>'


def address_line(lead, area_names):
    """Address plus the neighborhood name(s), for the addr line."""
    esc = html.escape
    address = esc(lead.get("address", ""))
    areas = esc(area_display(lead, area_names))
    if address and areas:
        return f"{address} &nbsp;&middot;&nbsp; {areas}"
    return address or areas


def build_html_row(lead, area_names):
    """Render one lead as a PRIVATE HTML table row (all values escaped)."""
    esc = html.escape

    maps_url = lead.get("google_maps_url", "")
    if maps_url:
        maps_cell = f'<a href="{esc(maps_url, quote=True)}" target="_blank">open</a>'
    else:
        maps_cell = '<span class="muted">&mdash;</span>'

    rating = lead.get("rating")
    if rating is None:
        rating_cell = '<span class="muted">no ratings</span>'
    else:
        rating_cell = f"{rating:.1f} &#9733; ({lead.get('review_count') or 0})"

    phone = esc(lead.get("phone", ""))
    phone_cell = phone if phone else '<span class="missing">none</span>'

    score = lead.get("lead_score", 0)
    confidence = lead.get("category_confidence", "medium")
    lead_type = str(lead.get("lead_type", "")).replace("_", " ")
    review_cell = (
        '<span class="review-yes">YES</span>'
        if lead.get("review_needed")
        else '<span class="muted">&mdash;</span>'
    )

    business_status = lead.get("business_status", "")
    closed_html = ""
    if business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        closed_html = (
            f'<div class="closed">{esc(business_status.replace("_", " ").title())}</div>'
        )

    notes = lead.get("notes", "")
    notes_html = f'<div class="notes">{esc(notes)}</div>' if notes else ""
    row_class = ' class="chain"' if (lead.get("cluster_size", 1) > 1) else ""

    return (
        f"    <tr{row_class}{data_areas_attr(lead)}>\n"
        f'      <td><span class="score {score_css_class(score)}">{score}</span></td>\n'
        f"      <td>{business_name_html(lead)}{closed_html}"
        f'<div class="addr">{address_line(lead, area_names)}</div>{notes_html}</td>\n'
        f'      <td>{esc(lead.get("matched_category") or lead.get("source_category", ""))}</td>\n'
        f'      <td><span class="badge conf-{esc(confidence)}">{esc(confidence)}</span></td>\n'
        f"      <td>{cluster_cell(lead)}</td>\n"
        f"      <td>{matches_cell(lead)}</td>\n"
        f"      <td>{website_cell(lead)}</td>\n"
        f"      <td>{presence_cell(lead)}</td>\n"
        f'      <td><span class="leadtype">{esc(lead_type)}</span></td>\n'
        f'      <td>{priority_badge(lead.get("manual_verification_priority", "medium"))}</td>\n'
        f'      <td>{priority_badge(lead.get("sales_priority", "low"))}</td>\n'
        f"      <td>{quick_links_cell(lead)}</td>\n"
        f"      <td>{phone_cell}</td>\n"
        f"      <td>{rating_cell}</td>\n"
        f"      <td>{maps_cell}</td>\n"
        f'      <td>{esc(lead.get("recommended_offer", ""))}</td>\n'
        f"      <td>{review_cell}</td>\n"
        "    </tr>"
    )


def build_mobile_card(lead, area_names):
    """Render one lead as a PRIVATE phone-friendly card (all values escaped)."""
    esc = html.escape

    score = lead.get("lead_score", 0)
    name_html = business_name_html(lead)

    business_status = lead.get("business_status", "")
    closed_html = ""
    if business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        closed_html = (
            f'<div class="closed">{esc(business_status.replace("_", " ").title())}</div>'
        )

    notes = lead.get("notes", "")
    notes_html = f'<div class="notes">{esc(notes)}</div>' if notes else ""

    badges = [
        website_status_badge(lead),
        presence_cell(lead),
        priority_badge(lead.get("manual_verification_priority", "medium"), "Verify: "),
        priority_badge(lead.get("sales_priority", "low"), "Sales: "),
    ]
    if lead.get("review_needed"):
        badges.append('<span class="review-yes">REVIEW</span>')
    if lead.get("cluster_size", 1) > 1 or lead.get("is_possible_chain"):
        size = lead.get("cluster_size", 1)
        badges.append(f'<span class="chainbadge">&#9939; {size} location(s)</span>')

    rating = lead.get("rating")
    if rating is None:
        rating_text = "no ratings"
    else:
        rating_text = f"{rating:.1f} &#9733; ({lead.get('review_count') or 0})"
    phone = esc(lead.get("phone", ""))
    phone_html = phone if phone else '<span class="missing">no phone</span>'
    category = esc(lead.get("matched_category") or lead.get("source_category", ""))
    lead_type = esc(str(lead.get("lead_type", "")).replace("_", " "))
    row2 = (
        f"{category} &nbsp;&middot;&nbsp; {lead_type} &nbsp;&middot;&nbsp; "
        f"{phone_html} &nbsp;&middot;&nbsp; {rating_text}"
    )

    buttons = []
    queries = quick_search_queries(lead)
    if queries:
        buttons.append(
            f'<a class="btn" href="https://www.google.com/search?q='
            f'{esc(queries["google"], quote=True)}" target="_blank">Google</a>'
        )
        buttons.append(
            f'<a class="btn" href="https://www.google.com/search?q='
            f'{esc(queries["instagram"], quote=True)}" target="_blank">Instagram</a>'
        )
        buttons.append(
            f'<a class="btn" href="https://www.google.com/search?q='
            f'{esc(queries["facebook"], quote=True)}" target="_blank">Facebook</a>'
        )
    maps_url = lead.get("google_maps_url") or ""
    if maps_url:
        buttons.append(
            f'<a class="btn btn-maps" href="{esc(maps_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">Maps</a>'
        )
    buttons_html = (
        f'  <div class="btnrow">{"".join(buttons)}</div>\n' if buttons else ""
    )

    card_class = "card chain" if lead.get("cluster_size", 1) > 1 else "card"
    return (
        f'<div class="{card_class}"{data_areas_attr(lead)}>\n'
        f'  <div class="cardtop"><span class="score {score_css_class(score)}">'
        f'{score}</span><span class="cardname">{name_html}</span></div>\n'
        f"{closed_html}"
        f'  <div class="addr">{address_line(lead, area_names)}</div>\n'
        f"{notes_html}"
        f'  <div class="badges">{"".join(badges)}</div>\n'
        f'  <div class="row2">{row2}</div>\n'
        f"{buttons_html}"
        "</div>"
    )


def build_public_row(lead, area_names):
    """Render one business as a PUBLIC directory table row.

    Only fields from PUBLIC_FIELDS may be used here: no scores, priorities,
    lead types, offers, notes, presence or cluster analysis.
    """
    esc = html.escape

    maps_url = lead.get("google_maps_url", "")
    if maps_url:
        maps_cell = f'<a href="{esc(maps_url, quote=True)}" target="_blank">open</a>'
    else:
        maps_cell = '<span class="muted">&mdash;</span>'

    website = lead.get("website") or ""
    if website:
        website_cell_html = f'<a href="{esc(website, quote=True)}" target="_blank">site</a>'
    else:
        website_cell_html = '<span class="muted">&mdash;</span>'

    rating = lead.get("rating")
    if rating is None:
        rating_cell = '<span class="muted">&mdash;</span>'
    else:
        rating_cell = f"{rating:.1f} &#9733; ({lead.get('review_count') or 0})"

    phone = esc(lead.get("phone", ""))
    phone_cell = phone if phone else '<span class="muted">&mdash;</span>'

    business_status = lead.get("business_status", "")
    closed_html = ""
    if business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        closed_html = (
            f'<div class="closed">{esc(business_status.replace("_", " ").title())}</div>'
        )

    return (
        f"    <tr{data_areas_attr(lead)}>\n"
        f"      <td>{business_name_html(lead)}{closed_html}"
        f'<div class="addr">{esc(lead.get("address", ""))}</div></td>\n'
        f'      <td>{esc(lead.get("matched_category") or lead.get("source_category", ""))}</td>\n'
        f"      <td>{esc(area_display(lead, area_names))}</td>\n"
        f"      <td>{phone_cell}</td>\n"
        f"      <td>{website_cell_html}</td>\n"
        f"      <td>{rating_cell}</td>\n"
        f"      <td>{maps_cell}</td>\n"
        "    </tr>"
    )


def build_public_card(lead, area_names):
    """Render one business as a PUBLIC directory card (same field rules)."""
    esc = html.escape

    business_status = lead.get("business_status", "")
    closed_html = ""
    if business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        closed_html = (
            f'<div class="closed">{esc(business_status.replace("_", " ").title())}</div>'
        )

    rating = lead.get("rating")
    rating_text = "" if rating is None else f"{rating:.1f} &#9733; ({lead.get('review_count') or 0})"
    phone = esc(lead.get("phone", ""))
    category = esc(lead.get("matched_category") or lead.get("source_category", ""))
    areas = esc(area_display(lead, area_names))
    row2_parts = [part for part in (category, areas, phone, rating_text) if part]
    row2 = " &nbsp;&middot;&nbsp; ".join(row2_parts)

    buttons = []
    if lead.get("phone"):
        tel = esc(str(lead["phone"]).replace(" ", ""), quote=True)
        buttons.append(f'<a class="btn" href="tel:{tel}">Call</a>')
    website = lead.get("website") or ""
    if website:
        buttons.append(
            f'<a class="btn" href="{esc(website, quote=True)}" target="_blank" '
            'rel="noopener noreferrer">Website</a>'
        )
    maps_url = lead.get("google_maps_url") or ""
    if maps_url:
        buttons.append(
            f'<a class="btn btn-maps" href="{esc(maps_url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">Maps</a>'
        )
    buttons_html = f'  <div class="btnrow">{"".join(buttons)}</div>\n' if buttons else ""

    return (
        f'<div class="card"{data_areas_attr(lead)}>\n'
        f'  <div class="cardname">{business_name_html(lead)}</div>\n'
        f"{closed_html}"
        f'  <div class="addr">{esc(lead.get("address", ""))}</div>\n'
        f'  <div class="row2">{row2}</div>\n'
        f"{buttons_html}"
        "</div>"
    )


def group_chains_for_display(leads):
    """Keep score order, but pull members of one brand cluster together.

    When the first (best-scoring) member of a cluster is emitted, its other
    branches follow immediately, ordered by their own score. Every branch
    stays visible — clusters group rows, they never hide them.
    """
    by_cluster = {}
    for lead in leads:
        by_cluster.setdefault(lead.get("cluster_id") or id(lead), []).append(lead)

    ordered, seen = [], set()
    for lead in leads:
        cluster_id = lead.get("cluster_id") or id(lead)
        if cluster_id in seen:
            continue
        seen.add(cluster_id)
        ordered.extend(by_cluster[cluster_id])  # already score-sorted
    return ordered


def write_csv(leads, path):
    # utf-8-sig so Excel on Windows opens accented names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            for column in LIST_COLUMNS:
                value = row.get(column)
                if isinstance(value, list):
                    row[column] = "; ".join(str(item) for item in value)
            writer.writerow(row)


def count_multi_location_groups(leads):
    """Distinct possible multi-location brand GROUPS (not strict clusters).

    Strict clusters only merge identical brand keys, so 'Montibello' and
    'MONTIBELLO Hair Lounge and MedSpa' are two clusters of one row each —
    counting clusters with 2+ members would report 0 multi-location brands
    while MULTI_LOCATION_BRAND_REVIEW rows sit in the table. Instead, every
    lead flagged is_possible_chain (2+ locations, likely name match, or
    known franchise) is bucketed by its core brand key, which is what links
    those lookalike rows in the first place.
    """
    groups = set()
    for lead in leads:
        if not lead.get("is_possible_chain"):
            continue
        groups.add(
            lead.get("core_brand_key")
            or lead.get("brand_key")
            or lead.get("cluster_id")
        )
    return len(groups)


def write_html(leads, path, generated_at, area_names):
    display_leads = group_chains_for_display(leads)
    unique_brand_keys = len({lead.get("brand_key") for lead in leads})
    review_rows = sum(1 for lead in leads if lead.get("review_needed"))
    potential = sum(
        1 for lead in leads if lead.get("lead_type") == "POTENTIAL_WEBSITE_LEAD"
    )
    verified_weak = sum(
        1 for lead in leads if lead.get("online_presence_status") == "weak_or_missing"
    )
    # Only shown when a manual check has actually confirmed a weak presence.
    verified_weak_html = (
        f" &nbsp;&middot;&nbsp; <b>{verified_weak} verified weak/missing presence lead(s)</b>"
        if verified_weak
        else ""
    )
    rows = "\n".join(build_html_row(lead, area_names) for lead in display_leads)
    page = HTML_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(leads),
        multi_group_count=count_multi_location_groups(leads),
        review_rows=review_rows,
        potential_count=potential,
        verified_weak_html=verified_weak_html,
        unique_brand_keys=unique_brand_keys,
        rows=rows,
        filter_css=FILTER_CSS,
        area_filter=build_area_filter(leads, area_names),
        filter_script=FILTER_SCRIPT,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)


def write_mobile_html(leads, path, generated_at, area_names):
    display_leads = group_chains_for_display(leads)
    cards = "\n".join(build_mobile_card(lead, area_names) for lead in display_leads)
    page = MOBILE_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(leads),
        cards=cards,
        filter_css=FILTER_CSS,
        area_filter=build_area_filter(leads, area_names),
        filter_script=FILTER_SCRIPT,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)


def public_sort_key(lead):
    """Neutral ordering for the public directory: category, then name."""
    category = (lead.get("matched_category") or lead.get("source_category") or "").lower()
    return (category, (lead.get("name") or "").lower())


def write_public_html(leads, path, generated_at, area_names):
    ordered = sorted(leads, key=public_sort_key)
    rows = "\n".join(build_public_row(lead, area_names) for lead in ordered)
    page = PUBLIC_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(ordered),
        rows=rows,
        filter_css=FILTER_CSS,
        area_filter=build_area_filter(ordered, area_names),
        filter_script=FILTER_SCRIPT,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)


def write_public_mobile_html(leads, path, generated_at, area_names):
    ordered = sorted(leads, key=public_sort_key)
    cards = "\n".join(build_public_card(lead, area_names) for lead in ordered)
    page = PUBLIC_MOBILE_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(ordered),
        cards=cards,
        filter_css=FILTER_CSS,
        area_filter=build_area_filter(ordered, area_names),
        filter_script=FILTER_SCRIPT,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)


def load_area_names():
    """Map area label -> human-readable name (tolerates config problems)."""
    try:
        return {area["label"]: area.get("name", area["label"]) for area in load_scan_areas()}
    except ConfigError:
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="Export scored leads: public directory + private research reports."
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="only export PRIVATE leads with at least this score (default: export all)",
    )
    args = parser.parse_args()

    try:
        data = load_json(SCORED_LEADS_FILE)
    except ConfigError as exc:
        die(f"{exc}\n  Run the pipeline first:\n"
            "    py scripts/scan_places.py --matrix\n"
            "    py scripts/score_leads.py")

    all_leads = data.get("leads", [])
    leads = all_leads
    if args.min_score is not None:
        leads = [lead for lead in leads if lead.get("lead_score", 0) >= args.min_score]
    if not leads:
        die("No leads to export. Run a scan + score first, or lower --min-score.")

    # Highest score first — that is the order you work the list in.
    leads.sort(key=lambda lead: lead.get("lead_score", 0), reverse=True)

    area_names = load_area_names()
    generated_at = utc_now_iso()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    # PRIVATE research reports (gitignored — never commit these).
    csv_path = PRIVATE_DIR / "leads.csv"
    json_path = PRIVATE_DIR / "leads.json"
    private_html_path = PRIVATE_DIR / "leads.html"
    private_mobile_path = PRIVATE_DIR / "leads_mobile.html"

    write_csv(leads, csv_path)
    save_json(
        json_path,
        {
            "generated_at": generated_at,
            "note": (
                "PRIVATE short-term lead PRIORITIZATION snapshot from the official "
                "Google Places API — not proof of anything, and never to be "
                "published. A missing websiteUri only means the Google profile "
                "returned no website; the business may still have Instagram/"
                "booking/directory presence or an unlinked site. "
                "manual_verification_priority = check this lead first; "
                "sales_priority = contact this lead first (never high until a "
                "manual check in config/manual_checks.local.yml confirms "
                "weak/missing presence). Re-scan before outreach."
            ),
            "lead_count": len(leads),
            "leads": leads,
        },
    )
    write_html(leads, private_html_path, generated_at, area_names)
    write_mobile_html(leads, private_mobile_path, generated_at, area_names)

    # PUBLIC directory pages (committed; served by GitHub Pages). Always
    # built from ALL leads — --min-score is a research filter, and ranking
    # information must not shape the public pages.
    public_html_path = REPORTS_DIR / "leads.html"
    public_mobile_path = REPORTS_DIR / "leads_mobile.html"
    write_public_html(all_leads, public_html_path, generated_at, area_names)
    write_public_mobile_html(all_leads, public_mobile_path, generated_at, area_names)

    print(f"Exported {len(leads)} leads (generated {generated_at}):")
    print("  PRIVATE (gitignored — do not commit or share):")
    print(f"    {csv_path}")
    print(f"    {json_path}")
    print(f"    {private_html_path}   <- your research view, open in a browser")
    print(f"    {private_mobile_path}   <- phone-friendly research cards")
    print("  PUBLIC directory (safe to commit; served by GitHub Pages):")
    print(f"    {public_html_path}")
    print(f"    {public_mobile_path}")


if __name__ == "__main__":
    main()
