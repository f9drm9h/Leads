# Phase 2 (NOT implemented): Text Search adapter for type-less trades

Status: **evaluation only** — do not build or run this until the Nearby
Search SDE pilot and the full matrix round are done and reviewed.

## Why Nearby Search cannot find these businesses

Nearby Search (New) filters by `includedTypes`, which must come from
**Table A** of the official place-types list. Verified against that list on
2026-07-10, none of these SDE-relevant trades has any usable type:

| Trade | Closest type checked | Result |
|---|---|---|
| Air-conditioning / refrigeration technicians | `air_conditioning_contractor` | does not exist (Table A or B) |
| Aluminum & glass contractors | `glass_repair_service`, `welding_service` | do not exist |
| Cabinet / kitchen manufacturers | `carpenter` | does not exist |
| Solar-panel / inverter installers | `solar_energy_contractor` | does not exist |
| Waterproofing services | — | nothing close exists |
| Custom doors & windows | — | nothing close exists |
| Photographers (event services) | `photographer`, `photography_studio` | do not exist |

`general_contractor` exists but only in **Table B** (response-only): it can
appear in results but is rejected as a Nearby Search filter. Forcing these
trades into wrong Table A types (e.g. scanning `electrician` hoping for AC
techs) produces low-confidence noise — exactly what the scoring engine
penalizes. Text Search (New) is the correct tool: it takes a free-text query
("tecnico de aire acondicionado") plus a location bias.

## Smallest safe implementation (when approved)

1. New config `config/text_searches.yml`:
   ```yaml
   searches:
     - label: ac_refrigeration
       query: "tecnico de aire acondicionado y refrigeracion"
       areas: [sde-villa-faro, sde-charles-de-gaulle, sde-san-isidro]
       category: home_services      # reuse its scoring flags (quote_based etc.)
   ```
2. One new function in `scripts/scan_places.py` calling
   `POST https://places.googleapis.com/v1/places:searchText` with:
   - the **same FieldMask** as Nearby Search (no new fields, no wildcard);
   - `locationBias.circle` from the area (Text Search's circular option is a
     *bias*, not a hard restriction — expect some out-of-area rows, which the
     normal address/area review catches);
   - `pageSize: 20`, no pagination (same 1-request-per-pair budget model).
3. Results go through the **existing** `normalize_place` → merge-by-place-id
   path with `source_category` set to the search label — dedup against
   Nearby results is automatic.
4. All existing guards apply unchanged: `--dry-run`, `--max-requests`,
   request logging, `PAUSE_BETWEEN_REQUESTS`.

Roughly 40 lines of new code plus config; no changes to scoring or export.

## Cost estimate

- 6 trade queries × 3 zones each = **18 requests per round**
  (worst case 6 × 4 = 24).
- Same FieldMask as today → **Text Search Enterprise** SKU
  (`nationalPhoneNumber`, `websiteUri`, `rating`, `userRatingCount` are
  Enterprise-tier fields), listed at **US$35 per 1,000 requests** with
  **1,000 free calls/month** for Enterprise-tier SKUs under the current
  (post-March-2025) per-SKU free-tier model. 18–24 requests ≈ US$0.63–0.84
  without the free tier, US$0 within it. Text Search and Nearby Search are
  separate SKUs, so each has its own free 1,000 calls.

## Preconditions before implementing

- Nearby Search pilot + full SDE matrix completed and reviewed.
- Owner approval for the extra request volume.
- Re-verify SKU pricing on the official pricing page (it changes).
