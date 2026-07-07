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
   scripts/scan_places.py     ->  data/scan_results.json   (raw, deduplicated by place id)
   scripts/score_leads.py     ->  data/leads_scored.json   (+ brand clusters + website
                                                            status + confidence + score)
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
    included_types: [beauty_salon, hair_salon, barber_shop, nail_salon, spa]
    also_match_types: [hair_care, skin_care_clinic]   # count as a match, not sent to API
    excluded_types: []              # types that mean "wrong kind of business"
    excluded_name_keywords: []      # name words that flag a mismatch
    included_name_keywords: [barberia, peluqueria]    # name words that support a match
    appointment_based: true    # customers book time slots
    quote_based: false         # customers ask for price quotes
    menu_based: false          # business sells from a menu/catalog
    question_heavy: false      # customers ask lots of questions before buying
    recommended_offer: "Appointment booking page with WhatsApp button"
```

`included_types` must be official place types from Google's "Table A" list:
<https://developers.google.com/maps/documentation/places/web-service/place-types>
An invalid type makes the API reject that request with a 400 error.

The other type/keyword lists are only used locally to judge **category
confidence**, so anything is safe there. Type patterns accept a `*` wildcard
at either end (`*_restaurant`); name keywords match whole words without
accents, and a trailing `*` matches prefixes (`auto*` matches "autopartes"
but plain `auto` does not match "autorizado").

- **high** confidence: the place's Google types match the category
- **medium**: types missing/generic, or the signals conflict — worth a look
- **low**: the types or name clearly point to a different kind of business
  (e.g. a refrigeration shop that showed up in the phone-repair scan)

Low-confidence leads are never deleted — they are scored down and marked
`NEEDS_MANUAL_REVIEW` so you can judge them yourself.

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

## Brand clusters — how branches are handled

Businesses are grouped by a normalized **brand key**: the name is lowercased,
accents and punctuation are removed, `(...)` branch hints and company
suffixes (SRL, S.R.L., S.A., RD, ...) are stripped. Leads whose keys are
**identical** form one cluster — there is no fuzzy matching, because merging
two unrelated businesses is worse than missing a branch.

Every lead gets: `brand_key`, `cluster_id`, `cluster_size`,
`is_possible_chain`, `other_locations_count` and `same_brand_locations`.
**Branches are grouped and labeled, never deleted** — every location stays
in the report.

Clusters are treated as *uncertain* when the shared name is generic
("Salon de Belleza" twice is probably two different businesses) or when the
brand is a known chain/franchise — those leads are flagged for manual review
instead of being trusted.

## Website status

**"Missing website" always means missing from that specific Google Places
profile — NOT necessarily missing from the whole company.** The status looks
across the brand cluster to tell those cases apart:

| `website_status` | Meaning |
|---|---|
| `has_website` | This profile links a website. |
| `brand_has_website_elsewhere` | This profile has no website, but another scanned location of the same brand does. Don't pitch a new site — fix the profile link. |
| `all_locations_missing_website` | No scanned location of this brand has a website. The strongest "needs a website" signal this tool can give. |
| `needs_manual_review` | No website on the profile and the brand cluster is uncertain (generic name or known chain) — verify by hand. |

Remember the tool only knows the locations **it scanned**: a brand can have
a website (or more branches) outside your scan areas. `all_locations_missing_website`
is evidence, not proof.

## Lead types

Each lead is classified so you can filter the list by the kind of work:
`NEW_WEBSITE_LEAD`, `GBP_CLEANUP_LEAD` (profile missing its brand's website
link), `BRANCH_PAGE_LEAD` (busy branch of a brand with a site elsewhere),
`MENU_PAGE_LEAD`, `QUOTE_FORM_LEAD`, `APPOINTMENT_PAGE_LEAD`,
`CHATBOT_CANDIDATE` (question-heavy category with 50+ reviews),
`NEEDS_MANUAL_REVIEW`, and `LOW_PRIORITY` (closed, or nothing to offer).

## How scoring works

| Points | Rule |
|-------:|------|
| +35 | every known location of this brand is missing a website |
| +20 | this specific profile is missing a website |
| +15 | phone number exists |
| +15 | rating ≥ 4.0 |
| +15 | review count ≥ 25 |
| +10 | category confidence is high |
| +10 | category is appointment-, quote-, or menu-based |
| +5  | business status is OPERATIONAL |
| −30 | the brand has a website on another location |
| −25 | category confidence is low |
| −25 | likely chain / franchise / corporate branch (3+ locations or known brand) |
| −50 | CLOSED_PERMANENTLY |
| −25 | CLOSED_TEMPORARILY |

All values are constants at the top of `scripts/score_leads.py` — tweak them
freely.

## How the recommended offer is chosen

Up to two suggestions per lead, driven by website status first:

1. `all_locations_missing_website` → *One-page website + WhatsApp button*
2. `brand_has_website_elsewhere` → *Google Business Profile cleanup: add the
   correct website link to this branch* (busy branches also get a
   *branch page on the existing brand website*)
3. `needs_manual_review` → *verify brand and branches manually before pitching*
4. Category flags: menu-based → *menu/catalog page or WhatsApp order flow*;
   quote-based (service/auto/events) → *quote request form*; otherwise
   appointment-based → *appointment request page*
5. FAQ chatbot is only suggested for `question_heavy` categories with
   50+ reviews (enough complexity to be worth automating)
6. Nothing matched → the category's `recommended_offer` from the config

## Before you contact anyone — please read

- This tool is a **prioritization aid, not a final truth source**. It ranks
  who is *probably* worth a look; it does not verify anything for you.
- "Missing website" means missing **from that Google Places profile**. Small
  businesses often have a site (or an Instagram that works as one) that
  simply isn't linked on Google. Check before pitching a new website —
  if the site exists, the real offer is a profile cleanup.
- **Multi-location businesses (any `cluster_size` > 1, any "Review?" flag)
  require manual verification before outreach.** Same-name places can be
  unrelated; branches can be run by different owners; chains have head
  offices that local branches can't decide for.
- **Refresh the data before contacting businesses** (re-run the scan).
  Places close, change phones and add websites all the time; the report
  shows `scanned_at` per lead so you can see how stale a row is.

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
- Requests use a **minimal field mask** — only the eleven fields the tool
  needs. The wildcard mask (`*`) is never used.

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
- Scan results **merge across runs** (newest scan of a place wins), and every
  area/category that ever found a place is kept in `source_areas` /
  `source_categories`. When a place was found by several categories, scoring
  uses the one its Google types fit best (`matched_category`).
- Brand clustering only merges **identical** normalized names — deliberately
  conservative. Rename lookalikes by hand if you know they're one brand.
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
