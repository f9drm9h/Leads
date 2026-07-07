"""Export scored leads to reports/leads.csv, leads.json and leads.html.

Reads  data/leads_scored.json  (produced by score_leads.py)

Usage:
    py scripts/export_report.py
    py scripts/export_report.py --min-score 50    (only stronger leads)

CSV and JSON are sorted by score. The HTML report is also score-ordered,
except that branches of the same brand cluster are pulled together under
the cluster's best-scoring member so possible chains are easy to spot.
"""

import argparse
import csv
import html
from string import Template

from common import (
    REPORTS_DIR,
    SCORED_LEADS_FILE,
    ConfigError,
    die,
    load_json,
    save_json,
    utc_now_iso,
)

CSV_COLUMNS = [
    "lead_score",
    "lead_type",
    "name",
    "matched_category",
    "category_confidence",
    "brand_key",
    "cluster_id",
    "cluster_size",
    "is_possible_chain",
    "other_locations_count",
    "website_status",
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
]

LIST_COLUMNS = {"same_brand_locations", "types", "source_categories", "source_areas"}

WEBSITE_STATUS_LABELS = {
    "has_website": "Has website",
    "brand_has_website_elsewhere": "Brand site elsewhere",
    "all_locations_missing_website": "No site (brand-wide)",
    "needs_manual_review": "Verify manually",
}

HTML_PAGE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local business leads</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         margin: 24px; color: #1c2733; background: #f6f8fa; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: #57606a; margin: 0 0 10px; font-size: 13px; }
  .legend { color: #57606a; font-size: 12px; margin: 0 0 18px; line-height: 1.7; }
  .legend b { color: #1c2733; }
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
  .cluster { font-size: 12px; }
  .chainbadge { color: #7a5b00; font-weight: 600; }
  .missing { color: #c0392b; font-weight: 600; }
  .review-yes { color: #a12318; font-weight: 700; }
  .addr { color: #57606a; font-size: 12px; }
  .notes { color: #8a6d1a; font-size: 12px; font-style: italic; max-width: 340px; }
  .muted { color: #8c959f; }
</style>
</head>
<body>
<h1>Local business leads</h1>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; $lead_count leads &nbsp;&middot;&nbsp;
$cluster_count brand clusters &nbsp;&middot;&nbsp; $chain_count possible multi-location brands &nbsp;&middot;&nbsp;
short-term research snapshot from the official Google Places API &mdash; re-scan before outreach.</p>
<p class="legend">
<b>Website status</b> is per Google profile, not per company:
<span class="badge ws-missing">No site (brand-wide)</span> no scanned location of this brand has a website &mdash; the best "needs a website" leads &nbsp;&middot;&nbsp;
<span class="badge ws-elsewhere">Brand site elsewhere</span> this profile has no website but another branch of the same brand does (fix the profile, don't sell a new site) &nbsp;&middot;&nbsp;
<span class="badge ws-review">Verify manually</span> the brand grouping is uncertain (generic name or known chain) &mdash; check by hand &nbsp;&middot;&nbsp;
<span class="badge ws-has">Has website</span> this profile links a website.<br>
Rows with a yellow tint belong to a possible multi-location brand &mdash; branches are grouped
under their best-scoring location but every branch is still listed.
Always verify chains and "Review?" rows manually before contacting anyone.
</p>
<div class="tablewrap">
<table>
  <thead>
    <tr>
      <th>Score</th><th>Business</th><th>Category</th><th>Conf.</th>
      <th>Brand cluster</th><th>Website status</th><th>Lead type</th>
      <th>Phone</th><th>Rating</th><th>Status</th><th>Maps</th>
      <th>Recommended offer</th><th>Review?</th>
    </tr>
  </thead>
  <tbody>
$rows
  </tbody>
</table>
</div>
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


def website_cell(lead):
    """Status badge (+ the most useful link we have) for the website column."""
    esc = html.escape
    status = lead.get("website_status", "")
    label = WEBSITE_STATUS_LABELS.get(status, status or "?")
    css = {
        "has_website": "ws-has",
        "brand_has_website_elsewhere": "ws-elsewhere",
        "all_locations_missing_website": "ws-missing",
        "needs_manual_review": "ws-review",
    }.get(status, "ws-review")
    parts = [f'<span class="badge {css}">{esc(label)}</span>']

    link = lead.get("website") or lead.get("brand_website_example") or ""
    if link:
        text = "site" if lead.get("website") else "brand site"
        parts.append(f'<a href="{esc(link, quote=True)}" target="_blank">{text}</a>')
    return "<br>".join(parts)


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


def build_html_row(lead):
    """Render one lead as an HTML table row (all values escaped)."""
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

    status = str(lead.get("business_status") or "").replace("_", " ").title()
    score = lead.get("lead_score", 0)
    confidence = lead.get("category_confidence", "medium")
    lead_type = str(lead.get("lead_type", "")).replace("_", " ")
    review_cell = (
        '<span class="review-yes">YES</span>'
        if lead.get("review_needed")
        else '<span class="muted">&mdash;</span>'
    )

    notes = lead.get("notes", "")
    notes_html = f'<div class="notes">{esc(notes)}</div>' if notes else ""
    row_class = ' class="chain"' if (lead.get("cluster_size", 1) > 1) else ""

    return (
        f"    <tr{row_class}>\n"
        f'      <td><span class="score {score_css_class(score)}">{score}</span></td>\n'
        f'      <td>{esc(lead.get("name", ""))}'
        f'<div class="addr">{esc(lead.get("address", ""))}</div>{notes_html}</td>\n'
        f'      <td>{esc(lead.get("matched_category") or lead.get("source_category", ""))}</td>\n'
        f'      <td><span class="badge conf-{esc(confidence)}">{esc(confidence)}</span></td>\n'
        f"      <td>{cluster_cell(lead)}</td>\n"
        f"      <td>{website_cell(lead)}</td>\n"
        f'      <td><span class="leadtype">{esc(lead_type)}</span></td>\n'
        f"      <td>{phone_cell}</td>\n"
        f"      <td>{rating_cell}</td>\n"
        f'      <td>{esc(status) or "?"}</td>\n'
        f"      <td>{maps_cell}</td>\n"
        f'      <td>{esc(lead.get("recommended_offer", ""))}</td>\n'
        f"      <td>{review_cell}</td>\n"
        "    </tr>"
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


def write_html(leads, path, generated_at):
    display_leads = group_chains_for_display(leads)
    clusters = {lead.get("cluster_id") for lead in leads if lead.get("cluster_id")}
    chains = {
        lead.get("cluster_id")
        for lead in leads
        if lead.get("cluster_id") and lead.get("cluster_size", 1) > 1
    }
    rows = "\n".join(build_html_row(lead) for lead in display_leads)
    page = HTML_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(leads),
        cluster_count=len(clusters),
        chain_count=len(chains),
        rows=rows,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(page)


def main():
    parser = argparse.ArgumentParser(
        description="Export scored leads to CSV, JSON and a static HTML report."
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="only export leads with at least this score (default: export all)",
    )
    args = parser.parse_args()

    try:
        data = load_json(SCORED_LEADS_FILE)
    except ConfigError as exc:
        die(f"{exc}\n  Run the pipeline first:\n"
            "    py scripts/scan_places.py --all\n"
            "    py scripts/score_leads.py")

    leads = data.get("leads", [])
    if args.min_score is not None:
        leads = [lead for lead in leads if lead.get("lead_score", 0) >= args.min_score]
    if not leads:
        die("No leads to export. Run a scan + score first, or lower --min-score.")

    # Highest score first — that is the order you work the list in.
    leads.sort(key=lambda lead: lead.get("lead_score", 0), reverse=True)

    generated_at = utc_now_iso()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = REPORTS_DIR / "leads.csv"
    json_path = REPORTS_DIR / "leads.json"
    html_path = REPORTS_DIR / "leads.html"

    write_csv(leads, csv_path)
    save_json(
        json_path,
        {
            "generated_at": generated_at,
            "note": (
                "Short-term lead research snapshot from the official Google Places "
                "API. 'Missing website' means missing from that Google Places "
                "profile, not necessarily from the whole company. Verify "
                "multi-location brands manually and refresh (re-scan) before outreach."
            ),
            "lead_count": len(leads),
            "leads": leads,
        },
    )
    write_html(leads, html_path, generated_at)

    print(f"Exported {len(leads)} leads (generated {generated_at}):")
    print(f"  {csv_path}")
    print(f"  {json_path}")
    print(f"  {html_path}   <- open this one in your browser")


if __name__ == "__main__":
    main()
