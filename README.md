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
config/scan_areas.yml   config/categories.yml   config/manual_checks.yml (+ .local)
config/scan_matrix.yml      |                     (your hand-verified results)
        \                   |
   scripts/scan_places.py     ->  data/scan_results.json   (raw, deduplicated by place id)
   scripts/score_leads.py     ->  data/leads_scored.json   (+ brand clusters + website
                                                            status + online presence +
                                                            confidence + score)
   scripts/export_report.py   ->  PUBLIC:  reports/leads.html + leads_mobile.html
                                           (neutral directory, committed / Pages)
                                  PRIVATE: private/leads.csv / leads.json /
                                           leads.html / leads_mobile.html
                                           (full research view, gitignored)
```

**This is a lead prioritization tool, not proof that a business lacks online
presence.** It tells you who is worth checking first — you must manually
verify top leads (the report gives you quick search links) before outreach.

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
py scripts/scan_places.py --area-prefix sde             # every sde-* area
py scripts/scan_places.py --matrix                      # targeted SDE scan matrix
py scripts/scan_places.py --matrix --dry-run            # preview, zero API calls
py scripts/scan_places.py --matrix --max-requests 40    # hard request cap
py scripts/scan_places.py --all                         # every area x category
py scripts/scan_places.py --all --fresh                 # discard old data first
py scripts/score_leads.py                               # rank the leads
py scripts/export_report.py                             # write the reports
py scripts/export_report.py --min-score 50              # private view: stronger leads only
```

Every run first prints the exact FieldMask, the billing SKU it triggers and
the planned request count. `--dry-run` shows the full request list without
calling the API (no key needed); `--max-requests N` stops before making more
than N requests; each real run is logged to `data/request_log.json` so you
can always check how many requests you made this month.

Then open **`private/leads.html`** in your browser (`private/leads_mobile.html`
is the same data as phone-friendly cards) — that is the research view with
scores and priorities. The files in `reports/` are the *public* directory
pages. In all reports, clicking a business name opens its Google Maps
profile in a new tab.

## Santo Domingo Este targeting

`config/scan_areas.yml` contains 12 `sde-*` circles (1.5–2.5 km, centers from
OpenStreetMap neighborhood nodes) covering Ensanche Ozama, Alma Rosa,
Los Mina, the Av. San Vicente de Paúl corridor, Villa Faro, La Isabelita,
Av. España, Av. Charles de Gaulle, Cancino, Invivienda, Hainamosa and
San Isidro / Prados de San Luis. `config/scan_matrix.yml` maps each area to
the 2–3 business categories that fit its commercial profile (36 requests
total) — run it with `--matrix`. Businesses with no valid Nearby Search type
(AC technicians, aluminum & glass, solar installers, ...) are deliberately
NOT forced into wrong categories; see `docs/text_search_phase2.md` for the
planned Text Search approach.

## Public vs. private outputs — what gets published

**This repository and its GitHub Pages site are public.** So the export step
writes two very different things:

- **PUBLIC (`reports/`, committed):** a neutral business directory only —
  name, category, neighborhood, address, publicly listed phone/website,
  rating, Maps link, plus a neighborhood filter. No scores, no priorities,
  no lead types, no offers, no notes, no presence analysis.
- **PRIVATE (`private/`, gitignored):** the full research view — scores,
  verify/sales priorities, presence analysis, recommended offers, notes,
  brand clusters, quick-search links, CSV and JSON. Never commit, publish
  or share these files.

Manual verification results follow the same split: the committed
`config/manual_checks.yml` may hold only neutral status values
(place_id + online_presence); all evidence and sales notes belong in
`config/manual_checks.local.yml` (gitignored, same format plus `note`).

Repeated scans **merge**: results are deduplicated by Google place id, and a
re-scan refreshes the stored data for the places it finds (this is how you
refresh data before an outreach round). Use `--fresh` to wipe and start over.

## Open the report on your phone (GitHub Pages)

The repository doubles as a small static site — but only the **public
directory pages** are published:

- `index.html` — landing page with two big buttons
- `reports/leads_mobile.html` — phone-friendly directory cards
- `reports/leads.html` — desktop directory table

**The private research reports never go online.** To use them on the go,
open `private/leads_mobile.html` locally or copy it to your phone yourself —
do not commit it, upload it, or serve it from Pages.

**Open any report locally:** double-click the HTML file — they are plain
HTML files and open in any browser without a server.

**Enable GitHub Pages** (one-time, already done for this repo):

1. On GitHub, open the repository → **Settings** → **Pages**.
2. Under **Build and deployment**, set **Source** to *Deploy from a branch*,
   pick the `main` branch and the `/ (root)` folder, then **Save**.
3. Wait a minute for the first build, then open
   `https://<your-username>.github.io/<repo>/`.

**Open it from an iPhone:** open the Pages URL in Safari, tap
**Open Mobile Directory**, then use Share → **Add to Home Screen** to keep it
one tap away. Business names open the Google Maps profile straight into the
Maps app.

**Privacy warning:** GitHub Pages sites are **public** even on a private
repository — anyone with the URL can read the published pages. That is why
only the neutral directory is committed: sales research (scores, priorities,
pitches, verification evidence) stays in the gitignored `private/` folder
and `config/manual_checks.local.yml`. Never commit those, `.env`, API keys
or other credentials (the `.gitignore` blocks all of them).

**Keep it fresh:** the published report is a snapshot. Before an outreach
round, re-run scan → score → export, then commit and push the regenerated
`reports/*.html` so the site shows current data.

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

## Website status vs. online presence — the crucial difference

**A missing `websiteUri` only means the website field was not returned by
the Places API for that Google profile.** Many businesses run entirely on
Instagram, Facebook, Fresha/Booksy, directories, or have a website that
simply isn't linked on Google. So the tool tracks two separate things:

`website_status` — what the **Google profiles** say (report label in quotes):

| `website_status` | Meaning |
|---|---|
| `has_website` | "Website on Google profile" — the profile links a site. |
| `brand_has_website_elsewhere` | "Brand site on another branch" — this profile has no site but another scanned location of the same brand does. Fix the profile link, don't pitch a new site. |
| `all_locations_missing_website` | "No website on Google profile" — the API returned no website for any scanned location of this brand. A lead worth **checking**, not a confirmed gap. |
| `needs_manual_review` | "Needs manual online-presence check" — uncertain brand grouping (generic name, known chain, or a likely name match). |

`online_presence_status` — what is actually known about the business online:
`unknown_not_checked` (default — the tool never probes social networks),
`weak_or_missing`, `has_social_presence`, `has_booking_presence`,
`has_directory_presence`, `has_website` (the verified values only come from
**your** entries in `config/manual_checks.yml` or
`config/manual_checks.local.yml`), and `needs_manual_review`.

## The manual verification workflow

1. Open `private/leads.html` and sort by score / "Verify priority" (`high` first).
2. Use each row's **quick search links** (Google, Instagram, Facebook site
   searches — generated links only, nothing is scraped) to check the
   business by hand.
3. Record what you found in `config/manual_checks.local.yml` (gitignored;
   `config/manual_checks.yml` explains the format; the `id` for each lead is
   in `private/leads.csv`). Only neutral status values may go in the
   committed `manual_checks.yml` — evidence and notes stay in the local file.
4. Re-run `py scripts/score_leads.py` and `py scripts/export_report.py`.

Only after step 3 can a lead become a `NEW_WEBSITE_LEAD` (+25 score) with
`sales_priority: high`. Verified social/booking/directory presence scores
the lead down (−15) so your list keeps getting cleaner as you work through
it. **Verify first, contact second** — the "Sales priority" column stays at
medium or below until you've done step 3 for that lead.

## Lead types

`POTENTIAL_WEBSITE_LEAD` (profile lacks a site, **presence not verified
yet** — the default for missing websites), `NEW_WEBSITE_LEAD` (only after
you manually verified weak/missing presence), `GBP_CLEANUP_LEAD` (profile
missing its brand's website link), `BRANCH_PAGE_LEAD` (busy branch of a
brand with a site elsewhere), `MENU_PAGE_LEAD`, `QUOTE_FORM_LEAD`,
`APPOINTMENT_PAGE_LEAD`, `CHATBOT_CANDIDATE` (question-heavy category with
50+ reviews), `MULTI_LOCATION_BRAND_REVIEW` (known chain or likely
same-brand name match — confirm before pitching),
`BAD_CATEGORY_MATCH` (name says plaza/mall/edificio/torre and the types
don't prove a real business in the niche), `NEEDS_MANUAL_REVIEW`, and
`LOW_PRIORITY` (closed, or nothing to offer).

Businesses with similar-but-not-identical names (e.g. "Montibello" and
"MONTIBELLO Hair Lounge and MedSpa") are **never auto-merged**; each lists
the other under `possible_brand_match` and both are flagged
`MULTI_LOCATION_BRAND_REVIEW` so you can decide.

## How scoring works

There is deliberately **no big bonus for a missing `websiteUri`** — only a
manual verification can prove weak online presence:

| Points | Rule |
|-------:|------|
| +15 | Google profile returned no `websiteUri` |
| +25 | **manually verified** as weak/missing online presence |
| +15 | phone number exists |
| +15 | rating ≥ 4.0 |
| +15 | review count ≥ 25 |
| +10 | category confidence is high |
| +10 | category is appointment-, quote-, or menu-based |
| +5  | business status is OPERATIONAL |
| −15 | verified Instagram/booking/directory presence (from manual checks) |
| −30 | the brand has a website on another scanned location |
| −25 | category confidence is low |
| −25 | bad category match (name says building/plaza, types don't disprove) |
| −25 | likely chain / franchise (3+ locations or known brand) |
| −20 | possible multi-location brand (2 locations or a likely name match) |
| −50 | CLOSED_PERMANENTLY |
| −25 | CLOSED_TEMPORARILY |

The −25 chain and −20 possible-multi penalties don't stack. All values are
constants at the top of `scripts/score_leads.py` — tweak them freely.

## Verify priority vs. sales priority — two different questions

Every lead gets **two** separate priorities, and mixing them up is how you
end up pitching a website to a business with 40k Instagram followers:

- `manual_verification_priority` (high / medium / low) — **"check this lead
  first."** How urgently a human should confirm the business's real online
  presence. **high** = promising unverified lead, check these first;
  **medium** = worth checking but murkier; **low** = verified already, has a
  site, or not worth the time.
- `sales_priority` (high / medium / low / skip) — **"contact this lead
  first."** How urgently the lead is worth outreach *given what has actually
  been verified*.

**Do not contact high-verification leads until their online presence has
been manually checked.** A lead can be high verification priority and only
medium (or low) sales priority at the same time — that combination means
"great on paper, but it may have strong Instagram, booking, or brand
presence the Places API can't see; check it before you call."

How `sales_priority` is assigned:

| Rule | Result |
|---|---|
| `lead_type` is `BAD_CATEGORY_MATCH` | **skip** |
| `lead_type` is `NEEDS_MANUAL_REVIEW` or `MULTI_LOCATION_BRAND_REVIEW` | **low** until verified |
| `online_presence_status` is `weak_or_missing` (manually verified) | **high** — the only path to high |
| Bank, arena, government/institutional place, mall, plaza, commercial building | **low** if category confidence is high, otherwise **skip** |
| Known chain/franchise that already has a website | **low** — head office decides, not the branch |
| `POTENTIAL_WEBSITE_LEAD` with verification priority high | **medium** until manually checked |
| `POTENTIAL_WEBSITE_LEAD`, anything else | **low** |
| Optimization leads (GBP cleanup, branch/menu/quote/appointment page, chatbot) | **medium** |
| `online_presence_status` is `unknown_not_checked` | never **high**, whatever else is true |

**For this side hustle, small local businesses are the main target.** Known
chains, franchises, banks, arenas, malls and other institutional brands are
usually low-priority or skip unless manually selected — a McDonald's branch
or a bank does not buy a one-page website from you, its head office decides
that. Verify priority stays separate on purpose: a chain can still be worth
*checking* for data quality, it just should not be a first-contact sales
lead. (Detection is local-only: Google place types plus whole-word name
keywords like banco/arena/estadio/plaza — the lists are constants at the
top of `scripts/score_leads.py`.)

The verification workflow is what moves leads: check a `Verify: high` row by
hand, record the result in `config/manual_checks.local.yml`, re-run score + export
— a confirmed gap jumps to `Sales: high` (`NEW_WEBSITE_LEAD`), a business
that turned out to have presence drops to low. Manually verifying a chain or
institutional place as `weak_or_missing` overrides the demotion — that is
how you deliberately pull one into the sales list.

## How the recommended offer is chosen

Up to two suggestions per lead. Wording stays non-definitive until you have
manually verified the lead:

1. `POTENTIAL_WEBSITE_LEAD` → *check online presence first (quick links in
   report); if weak: one-page website + WhatsApp button*
2. `NEW_WEBSITE_LEAD` (verified) → *one-page website + WhatsApp button*
3. `GBP_CLEANUP_LEAD` / `BRANCH_PAGE_LEAD` → *Google Business Profile
   cleanup: add the correct website link to this branch* (busy branches also
   get a *branch page on the existing brand website*)
4. `MULTI_LOCATION_BRAND_REVIEW` / `NEEDS_MANUAL_REVIEW` /
   `BAD_CATEGORY_MATCH` → verify manually before pitching anything
5. Category flags: menu-based → *menu/catalog page or WhatsApp order flow*;
   quote-based (service/auto/events) → *quote request form*; otherwise
   appointment-based → *appointment request page*
6. FAQ chatbot is only suggested for `question_heavy` categories with
   50+ reviews (enough complexity to be worth automating)
7. Nothing matched → the category's `recommended_offer` from the config

## Before you contact anyone — please read

- This tool is a **prioritization aid, not a final truth source**. It ranks
  who is *probably* worth a look; it does not verify anything for you.
- "No website on Google profile" means the website was **not returned by the
  Places API for that profile**. Small businesses often run on Instagram,
  Facebook, Fresha or a site that simply isn't linked on Google. Use the
  report's quick search links to check, record the result in
  `config/manual_checks.local.yml`, and re-run the score/export steps — if a
  site exists, the real offer is a profile cleanup, not a new website.
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
  depends on which fields you ask for. The contact + rating fields
  (`nationalPhoneNumber`, `websiteUri`, `rating`, `userRatingCount`) put this
  scanner in the **Nearby Search Enterprise** SKU — US$35 per 1,000 requests
  at the time of writing, with (under the current per-SKU free-tier model
  that replaced the old US$200 monthly credit in March 2025) **1,000 free
  Enterprise-tier calls per month**. Check current numbers at
  <https://developers.google.com/maps/billing-and-pricing/pricing>. Every run
  prints the exact FieldMask and SKU before any request is made. No
  Atmosphere fields (reviews, photos, summaries) are ever requested, and the
  wildcard mask is never used.
- One scan run = one request per (area × category) pair, each returning at
  most 20 places. `--matrix` = 36 requests; `--all` with the current configs
  = 14 × 6 = **84 requests** — prefer `--matrix`. Preview any run with
  `--dry-run` (zero API calls) and cap it with `--max-requests N`. Every real
  run is appended to `data/request_log.json`.
- A **budget alert is not a spending cap** — it only emails you. For a hard
  stop, set a **quota limit** in Google Cloud Console: *APIs & Services →
  Places API (New) → Quotas → "Requests per day"* — cap it at e.g. 100/day.
  Recommended, but only you should change your Cloud account settings.

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
- Raw and scored data live in `data/` (gitignored); the public directory in
  `reports/`; the research reports in `private/` (gitignored). All of it is
  disposable by design.
- Private reports are sorted by score, highest first; the public directory
  is sorted by category then name and carries no ranking information.
- Offers are capped at 2 per lead to keep pitches simple.
- No database, dashboard, CRM, or automation — by design, this is v1.

## Project layout

```
local-business-leads/
├── README.md
├── index.html            # GitHub Pages landing page (public directory)
├── .env.example          # template for your API key (copy to .env)
├── requirements.txt
├── config/
│   ├── scan_areas.yml    # where to scan (incl. the 12 sde-* circles)
│   ├── scan_matrix.yml   # targeted SDE area x category scan plan
│   ├── categories.yml    # what kinds of businesses to look for
│   ├── manual_checks.yml # committed: neutral verification statuses only
│   └── manual_checks.local.yml  # gitignored: evidence + sales notes
├── scripts/
│   ├── common.py         # shared helpers (paths, config loading, JSON)
│   ├── scan_places.py    # step 1: call the Places API (--matrix/--dry-run/...)
│   ├── score_leads.py    # step 2: score + recommend an offer
│   └── export_report.py  # step 3: public directory + private research reports
├── tests/                # py -m unittest discover -s tests
├── docs/
│   └── text_search_phase2.md  # planned (NOT implemented) Text Search adapter
├── data/                 # created by the scripts (short-term working data,
│                         # incl. request_log.json — your API usage log)
├── reports/              # PUBLIC directory pages (committed, Pages)
└── private/              # PRIVATE research reports (gitignored, never commit)
```
