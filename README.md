# Dark Funnel — Intent Detection

A single-repo full-stack tool that identifies anonymous website visitors from
target companies and scores their buying intent. Ships with synthetic seed
data and works end-to-end with **zero external API keys**.

**Stack:** FastAPI + SQLAlchemy + SQLite (backend) · plain HTML/JS (frontend,
no build step) · Anthropic Claude Sonnet 4.6 for summaries (optional, has
template fallback).

## Contents

- [`backend/`](./backend) — FastAPI app, models, resolver, scorer, seeder
- [`backend/seed_data.py`](./backend/seed_data.py) — synthetic event generator
- [`backend/target_accounts.csv`](./backend/target_accounts.csv) — seed CRM list
- [`frontend/public/index.html`](./frontend/public/index.html) — the dashboard

---

## Quick start (local)

```bash
# 1. Install backend deps
cd backend
pip install -r requirements.txt sqlalchemy

# 2. (Optional) drop in real API keys
cat > .env <<EOF
DATABASE_URL=sqlite:///./dark_funnel.db
INTENT_ALERT_THRESHOLD=70

# Optional — leave blank to use the deterministic mock resolver / template summarizer
IPINFO_TOKEN=
EMERGENT_LLM_KEY=          # or ANTHROPIC_API_KEY=...
CORS_ORIGINS=*
EOF

# 3. Seed 200 synthetic events across 15 fake companies + 20 target accounts
python seed_data.py

# 4. Start the API
uvicorn server:app --reload --host 0.0.0.0 --port 8001
```

Serve the dashboard however you like — the simplest way is to open
`frontend/public/index.html` directly (all fetches are relative to `/api`).
For local dev, run a tiny static server:

```bash
cd frontend/public
python -m http.server 3000
# then browse to http://localhost:3000
```

If the frontend and backend are on different ports locally, either:
- put the API behind a reverse-proxy at the same origin, **or**
- edit the single `API` constant near the top of `<script>` in `index.html`
  to `http://localhost:8001/api`.

In this Emergent preview, the frontend is served on port 3000 and API calls
are transparently routed to the backend on port 8001 through the
`/api` prefix, so nothing needs to change.

---

## Environment variables

| Variable                  | Default                              | Meaning |
|---------------------------|--------------------------------------|---------|
| `DATABASE_URL`            | `sqlite:////app/backend/dark_funnel.db` | SQLAlchemy DSN. Swap for `postgresql+psycopg://...` to migrate. |
| `INTENT_ALERT_THRESHOLD`  | `70`                                 | Score at or above which alerts fire. |
| `IPINFO_TOKEN`            | *(unset)*                            | If set, real ipinfo.io free-tier lookups. Otherwise the deterministic mock resolver runs. |
| `EMERGENT_LLM_KEY`        | *(unset)*                            | Emergent's universal Anthropic key. |
| `ANTHROPIC_API_KEY`       | *(unset)*                            | Falls back to native Anthropic key if `EMERGENT_LLM_KEY` isn't set. |
| `CORS_ORIGINS`            | `*`                                  | Comma-separated allowlist. |

**Everything is optional.** With no keys set at all, the app is fully
functional using the mock resolver and the template-based session summariser.

---

## Architecture

```
┌───────────────┐    /api/events        ┌──────────────────┐
│  Dashboard    │─────POST──────────▶  │  ip_resolver     │
│  (plain HTML) │                       │  matcher         │
│  polls /api   │◀────/api/visits ─────│  scoring         │
│  every 5s     │◀────/api/alerts ─────│  summarizer      │
└───────────────┘                       │  alerting (stub) │
                                        └────────┬─────────┘
                                                 ▼
                                            SQLite
```

Key modules (in `backend/`):

- **`ip_resolver.py`** — `resolve_ip(ip) -> {company, domain, industry, traffic_type}`.
  Uses `ipinfo.io` when `IPINFO_TOKEN` is set; otherwise a deterministic
  `/24`-subnet map to seeded companies. LRU-cached.
- **`matcher.py`** — exact + fuzzy domain match against the target-account
  list. Strips `www.`, `m.`, etc.; falls back to a difflib ratio ≥ 0.9.
- **`scoring.py`** — 0-100 intent score. All weights are **configurable
  constants at the top of the file** (page weights, duration bonus, repeat
  bonus, tier bonus). Tune to taste.
- **`summarizer.py`** — one-sentence session summary via `claude-sonnet-4-6`
  (Emergent LLM key or `ANTHROPIC_API_KEY`). Falls back to a template so
  the app never breaks if the key is absent or the call errors out.
- **`alerting.py`** — `send_to_slack()` and `update_hubspot_property()`
  stubs with commented-out `requests.post` / `requests.patch` blocks
  showing exactly where to drop your webhook URL and HubSpot bearer token.
- **`seed_data.py`** — generates 200 events across 15 fake companies over
  the past 14 days (deterministic seed = 42). About 30 % of events belong
  to multi-page sessions (repeat visitors).

---

## Key API endpoints

| Method | Path                     | Description |
|--------|--------------------------|-------------|
| `GET`  | `/api/`                  | Health / config. |
| `GET`  | `/api/stats`             | Counts (total, matched, high-intent, alerts, target accounts). |
| `POST` | `/api/events`            | Ingest a single visitor pageview. Auto-resolves, matches, scores, alerts. |
| `GET`  | `/api/events`            | List raw events. Params: `limit`, `high_intent_only`, `matched_only`. |
| `GET`  | `/api/visits/matched`    | Per-company aggregated dashboard view. `high_intent_only=true` supported. |
| `GET`  | `/api/accounts`          | List target accounts. |
| `POST` | `/api/accounts`          | Add a target account (`{company_name, domain, industry, crm_owner, tier}`). |
| `DELETE` | `/api/accounts/{id}`   | Remove a target account. |
| `POST` | `/api/accounts/import`   | CSV upload (same schema as `target_accounts.csv`). |
| `GET`  | `/api/alerts`            | Most recent alerts. |
| `POST` | `/api/alerts/regenerate` | Fire alerts for any existing high-intent matched events that don't have one yet (used after seeding). |

---

## Assumptions I made

Since the brief said "make reasonable assumptions and note them here":

1. **`claude-sonnet-4-6`** was the model requested. Since a bare
   `ANTHROPIC_API_KEY` wasn't supplied, I default to `EMERGENT_LLM_KEY`
   (Emergent's universal Claude/OpenAI/Gemini key). Setting either works;
   the summariser tries `EMERGENT_LLM_KEY` first, then `ANTHROPIC_API_KEY`,
   then falls back to the template.
2. **Frontend served on port 3000** in this preview environment (React CRA
   dev server), but the actual dashboard is 100 % plain HTML/JS/CSS living
   in `frontend/public/index.html`. React is a no-op (`App.js` returns `null`).
   Locally, you can just open the HTML file directly.
3. **Score threshold defaults to 70** as specified — configurable via
   `INTENT_ALERT_THRESHOLD`.
4. **Seeded target accounts (20)** are famous fictional companies so the
   demo is instantly recognisable. Delete/replace via the "Manage accounts"
   modal or CSV import.
5. **IP → company** in the mock resolver uses a stable `/24` subnet map so
   the same seed IP always resolves the same way — makes the demo
   reproducible across runs (`random.seed(42)` in the seeder).
6. **CRM owners** are made-up names spread across the target accounts;
   real orgs would sync these from Salesforce/HubSpot.
7. **Repeat-visit window** for the +15 bonus defaults to 48 hours (matches
   the requested "returned within 48h" wording).
8. **Session grouping** in the seed script re-uses `session_id` for ~30 %
   of subsequent events with the same IP within an hour, producing
   realistic multi-page sessions.
9. **Live updates** are simple 5-second polling. Not websockets — this is
   a demo, and polling is trivial to inspect from DevTools.

---

## Known limitations

- **Residential / remote workers.** ipinfo (and every commercial IP-to-company
  vendor) can't reliably reverse-map a home ISP IP to the visitor's employer.
  Expect meaningful "unknown" and "residential" traffic in real deployments;
  a lot of high-intent buyers browse from home. The `traffic_type` field is
  the honest signal to filter on.
- **Match precision.** Exact domain match is safe; the fuzzy 0.9-ratio step
  can occasionally match sibling brands (`acme.com` vs `acmehr.com`).
  Consider disabling the fuzzy tier before going to production.
- **Scoring is heuristic.** The starting weights in `scoring.py` are
  opinionated defaults — tune against your own funnel with a labelled set
  of known-won / known-lost deals.
- **Alert deduping** currently fires one alert per crossing event. In
  production you probably want per-account-per-day dedupe, working-hours
  suppression, and re-alert cool-downs.
- **AI summary cost.** Every alert issues one Claude call. Batch or cache
  per-company-per-day if you scale volume.

## Privacy & compliance considerations

Visitor de-anonymisation via IP is legally sensitive.

- **GDPR (EU / UK):** IPs can be personal data. You almost certainly need
  a legitimate-interest assessment (LIA), a cookie/consent banner (even
  though there's no cookie here, ePrivacy applies to any device-side data
  access), and a DPIA if this is used for automated profiling / individual
  targeting. Company-level enrichment ("someone at Acme visited pricing")
  is usually easier to defend than individual-level, but only if you have
  controls preventing individual identification.
- **CCPA / CPRA (California):** treat resolved company + IP as personal
  information; provide the "Do Not Sell / Share" opt-out and honour Global
  Privacy Control (GPC).
- **Retention.** Don't keep raw IPs longer than you need for scoring —
  rotate to hashed/truncated forms after N days.
- **Sensitive segments.** Health, minors, protected classes — de-anonymise
  with extra care or not at all.
- **Contractual.** Some IP-intel vendors' terms forbid re-selling or
  publishing resolved company data; check before exposing it to end users.

This app is a **demo**. Before pointing it at real traffic in the EU / UK /
California, get privacy counsel involved.
