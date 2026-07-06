# Local Business Leads

A small, local Python tool that finds nearby businesses with a **weak or
missing online presence**, using the **official Google Places API
(Nearby Search New)** — no scraping. It helps you research which small
businesses might benefit from simple digital services:

- one-page website + WhatsApp contact button
- menu / service / catalog page
- appointment booking page
- quote request form
- Google Business Profile cleanup
- basic FAQ chatbot

This is a **short-term lead research assistant**, not a permanent copy of
Google Maps data. The workflow is: scan → score → export a report → do your
outreach → re-scan next time for fresh data.

## How it works

```
config/scan_areas.yml     config/categories.yml
        \                   /
   scripts/scan_places.py     ->  data/scan_results.json   (raw, deduplicated)
   scripts/score_leads.py     ->  data/leads_scored.json   (+ score + offer)
   scripts/export_report.py   ->  reports/leads.csv / leads.json / leads.html
```

The report files in `reports/` are created when you run the export step —
they don't exist until then.

## Requirements

- Python 3.9 or newer
  (on Windows the command is usually `py`; on Mac/Linux use `python3`)
- A Google Cloud project with **Places API (New)** enabled and billing set up

## Setup

### 1. Install dependencies

Open a terminal in this folder and run:

```
py -m pip install -r requirements.txt
```

### 2. Get a Google Places API key

1. Go to <https://console.cloud.google.com> and sign in.
2. Create a project (or pick an existing one).
3. In **APIs & Services → Library**, search for **Places API (New)** and
   click **Enable**. (The "New" one — not the legacy "Places API".)
4. In **APIs & Services → Credentials**, click **Create credentials → API key**.
5. Recommended: click the key and restrict it so it can only call
   *Places API (New)*.
6. Billing must be enabled on the project for the API to respond.

### 3. Create your .env file

Copy `.env.example` to a new file named `.env` in this folder and paste your
key into it:

```
GOOGLE_PLACES_API_KEY=your-real-key-here
```

The key is read from this environment variable only. It is never hardcoded,
and `.env` is listed in `.gitignore` so it can't be committed by accident.

## Configure scan areas

Edit `config/scan_areas.yml`. Each area is a circle:

```yaml
areas:
  - label: default          # the name you type on the command line
    latitude: 18.4861
    longitude: -69.9312
    radius_meters: 3000     # max 50000 (Google API limit)
```

To get coordinates: right-click any spot in Google Maps and click the numbers
that pop up — they're copied as `latitude, longitude`.

**Tip:** Nearby Search returns at most **20 places per request** with no
paging, so several small circles cover a city much better than one huge one.

## Configure categories

Edit `config/categories.yml`. Five samples are included (salons,
phone repair, auto services, restaurants, event services):

```yaml
categories:
  - label: salons
    included_types: [beauty_salon, hair_salon, barber_shop, nail_salon]
    appointment_based: true    # customers book time slots
    quote_based: false         # customers ask for price quotes
    menu_based: false          # business sells from a menu/catalog
    recommended_offer: "Appointment booking page with WhatsApp button"
```

`included_types` must be official place types from Google's "Table A" list:
<https://developers.google.com/maps/documentation/places/web-service/place-types>
An invalid type makes the API reject that request with a 400 error.

## Run it

```
py scripts/scan_places.py --list                        # show configured labels
py scripts/scan_places.py --area default --category salons
py scripts/scan_places.py --area default                # all categories, one area
py scripts/scan_places.py --all                         # every area x category
py scripts/scan_places.py --all --fresh                 # discard old data first
py scripts/score_leads.py                               # rank the leads
py scripts/export_report.py                             # write the 3 reports
py scripts/export_report.py --min-score 50              # only stronger leads
```

Then open `reports/leads.html` in your browser.

Repeated scans **merge**: results are deduplicated by Google place id, and a
re-scan refreshes the stored data for the places it finds (this is how you
refresh data before an outreach round). Use `--fresh` to wipe and start over.

## How scoring works

| Points | Rule |
|-------:|------|
| +35 | website is missing |
| +20 | phone number exists |
| +15 | rating ≥ 4.0 |
| +15 | review count ≥ 25 |
| +15 | category is appointment-, quote-, or menu-based |
| +10 | Google Maps URL exists |
| +5  | business status is OPERATIONAL |
| −50 | CLOSED_PERMANENTLY |
| −25 | CLOSED_TEMPORARILY |
| −10 | phone number is missing |
| −10 | rating < 3.5 with ≥ 10 reviews |

All values are constants at the top of `scripts/score_leads.py` — tweak them
freely.

## How the recommended offer is chosen

Up to two suggestions per lead, in priority order:

1. No website → *one-page website with WhatsApp button*
2. Category flags → *menu/catalog page*, *appointment booking page*,
   and/or *quote request form*
3. Weak Google profile (no phone, no Maps link, or under 10 reviews)
   → *Google Business Profile cleanup*
4. Has a website and 50+ reviews → *FAQ chatbot*
5. Nothing matched → the category's `recommended_offer` from the config

## Compliance notes — please read

- This tool only calls the **official Google Places API**. It never scrapes
  the Google Maps website, and you shouldn't either.
- **You are responsible for reviewing Google's current Places API Terms of
  Service, pricing, caching and storage rules.** Google restricts how long
  most Places content may be cached or stored (place IDs are treated
  differently from other fields). Start here:
  - <https://developers.google.com/maps/documentation/places/web-service/policies>
  - <https://cloud.google.com/maps-platform/terms>
- Treat the output as a **short-term research snapshot**: every report embeds
  a `generated_at` timestamp and every lead a `scanned_at` timestamp. Re-scan
  before outreach and delete stale snapshots (`data/` and `reports/`) when a
  research round is done.
- Requests use a **minimal field mask** — only the ten fields the tool needs.
  The wildcard mask (`*`) is never used.

## Cost control

- **The field mask decides the price.** Each request's billing tier (SKU)
  depends on which fields you ask for. The contact fields
  (`nationalPhoneNumber`, `websiteUri`) put Nearby Search into a higher tier
  than basic fields — check current pricing at
  <https://developers.google.com/maps/billing-and-pricing>. If you want
  cheaper scans, remove those two fields from `FIELD_MASK` in
  `scripts/scan_places.py` (phone/website scoring then degrades).
- One scan run = (selected areas × selected categories) requests, each
  returning at most 20 places. `--all` with the sample configs = 2 × 5 =
  10 requests. Start with one area and one category.
- Set a **budget alert and quota caps** in Google Cloud so a mistake can't
  become an expensive surprise. Google Maps Platform includes some free
  monthly usage — check the current amount, it changes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `GOOGLE_PLACES_API_KEY is not set` | Copy `.env.example` to `.env` and paste your key (Setup step 3). |
| `API error PERMISSION_DENIED / 403` | Key is wrong or restricted, *Places API (New)* isn't enabled, or billing is off. |
| `API error INVALID_ARGUMENT / 400` | Usually a typo'd place type in `categories.yml`, or `radius_meters` > 50000. |
| `API error 429` | Quota exceeded — wait a bit or raise quotas in Google Cloud. |
| `No businesses found` | Increase the radius, check the coordinates, or try broader place types. |
| `'python' is not recognized` (Windows) | Use `py` instead, or install Python from python.org with "Add to PATH" checked. |
| Network/SSL errors | Retry; check VPN/proxy/firewall. The script reports the failure and continues with the next request. |
| Corrupted data file | Re-run the scan with `--fresh`, or delete the `data/` folder. |

## Defaults and decisions made for you

- Sample scan areas point at Santo Domingo, DR — replace with your own.
- Scan results **merge across runs** (newest scan of a place wins). If the
  same place matches two categories, the most recent scan decides its
  category.
- Raw and scored data live in `data/` (gitignored); polished reports in
  `reports/`. Both are disposable by design.
- Reports are sorted by score, highest first.
- Offers are capped at 2 per lead to keep pitches simple.
- No database, dashboard, CRM, or automation — by design, this is v1.

## Project layout

```
local-business-leads/
├── README.md
├── .env.example          # template for your API key (copy to .env)
├── requirements.txt
├── config/
│   ├── scan_areas.yml    # where to scan
│   └── categories.yml    # what kinds of businesses to look for
├── scripts/
│   ├── common.py         # shared helpers (paths, config loading, JSON)
│   ├── scan_places.py    # step 1: call the Places API
│   ├── score_leads.py    # step 2: score + recommend an offer
│   └── export_report.py  # step 3: write CSV / JSON / HTML reports
├── data/                 # created by the scripts (short-term working data)
└── reports/              # leads.csv, leads.json, leads.html (generated)
```
