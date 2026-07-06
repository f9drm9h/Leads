"""Export scored leads to reports/leads.csv, leads.json and leads.html.

Reads  data/leads_scored.json  (produced by score_leads.py)

Usage:
    py scripts/export_report.py
    py scripts/export_report.py --min-score 50    (only stronger leads)
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
    "name",
    "source_category",
    "primary_type",
    "phone",
    "website_status",
    "website",
    "rating",
    "review_count",
    "business_status",
    "address",
    "google_maps_url",
    "recommended_offer",
    "source_area",
    "scanned_at",
]

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
  .meta { color: #57606a; margin: 0 0 20px; font-size: 13px; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); font-size: 14px; }
  th, td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0;
           text-align: left; vertical-align: top; }
  th { background: #eef2f6; position: sticky; top: 0; }
  tr:hover td { background: #f0f6ff; }
  .score { display: inline-block; min-width: 34px; text-align: center;
           padding: 2px 8px; border-radius: 12px; font-weight: 600; color: #fff; }
  .score-high { background: #1a7f37; }
  .score-mid  { background: #b58a00; }
  .score-low  { background: #8c959f; }
  .missing { color: #c0392b; font-weight: 600; }
  .addr { color: #57606a; font-size: 12px; }
  .muted { color: #8c959f; }
</style>
</head>
<body>
<h1>Local business leads</h1>
<p class="meta">Generated: $generated_at &nbsp;&middot;&nbsp; $lead_count leads &nbsp;&middot;&nbsp;
Short-term research snapshot from the official Google Places API &mdash;
re-run the scan for fresh data before outreach.</p>
<table>
  <thead>
    <tr>
      <th>Score</th><th>Business</th><th>Category</th><th>Phone</th>
      <th>Website</th><th>Rating</th><th>Status</th><th>Maps</th>
      <th>Recommended offer</th><th>Scanned at</th>
    </tr>
  </thead>
  <tbody>
$rows
  </tbody>
</table>
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


def build_html_row(lead):
    """Render one lead as an HTML table row (all values escaped)."""
    esc = html.escape

    website = lead.get("website", "")
    if website:
        website_cell = f'<a href="{esc(website, quote=True)}" target="_blank">website</a>'
    else:
        website_cell = '<span class="missing">MISSING</span>'

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

    return (
        "    <tr>\n"
        f'      <td><span class="score {score_css_class(score)}">{score}</span></td>\n'
        f'      <td>{esc(lead.get("name", ""))}'
        f'<div class="addr">{esc(lead.get("address", ""))}</div></td>\n'
        f'      <td>{esc(lead.get("source_category", ""))}</td>\n'
        f"      <td>{phone_cell}</td>\n"
        f"      <td>{website_cell}</td>\n"
        f"      <td>{rating_cell}</td>\n"
        f'      <td>{esc(status) or "?"}</td>\n'
        f"      <td>{maps_cell}</td>\n"
        f'      <td>{esc(lead.get("recommended_offer", ""))}</td>\n'
        f'      <td>{esc(lead.get("scanned_at", ""))}</td>\n'
        "    </tr>"
    )


def write_csv(leads, path):
    # utf-8-sig so Excel on Windows opens accented names correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            row["website_status"] = "has website" if lead.get("website") else "MISSING"
            writer.writerow(row)


def write_html(leads, path, generated_at):
    rows = "\n".join(build_html_row(lead) for lead in leads)
    page = HTML_PAGE.substitute(
        generated_at=html.escape(generated_at),
        lead_count=len(leads),
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
                "API. Refresh (re-scan) before outreach; do not treat as a permanent database."
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
