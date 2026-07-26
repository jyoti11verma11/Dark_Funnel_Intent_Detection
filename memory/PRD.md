# PRD — Dark Funnel Intent Detection

## Original problem statement
Build a full-stack Dark Funnel Intent Detection web app — a tool that
identifies anonymous website visitors from target companies and scores
their buying intent. Stack: FastAPI + SQLAlchemy + SQLite backend, plain
HTML/JS frontend (no build step, no framework). Ships with synthetic
seed data and works end-to-end with zero API keys.

## User personas
- **Sales rep** — checks the dashboard mid-day to see which target
  accounts are hot; opens the alert feed for Claude-summarised context
  and hands the account to the mapped CRM owner.
- **RevOps / SDR manager** — tunes the scoring weights, curates the
  target account list, wires real Slack + HubSpot webhooks.
- **Engineer evaluating the demo** — inspects the plain HTML, edits
  weights in `scoring.py`, swaps SQLite for Postgres via `DATABASE_URL`.

## Core requirements (static)
1. Synthetic event generator: 200 events / 15 companies / 14 days.
2. Pluggable IP→company resolver: ipinfo.io + deterministic mock fallback.
3. Editable target-account list (20 seeded, CSV import, admin UI).
4. Fuzzy domain matcher (exact + endswith + difflib ≥0.9).
5. Configurable intent scorer (0-100, weights at top of `scoring.py`).
6. AI session summariser: Claude Sonnet 4.6 via Emergent LLM key, with
   deterministic template fallback if no key.
7. Alerting with stubbed Slack + HubSpot integrations, threshold=70.
8. Sortable dashboard: matched visits table with high-intent filter and
   live-polling alerts feed.
9. README with setup, env vars, architecture, limitations, and
   GDPR/CCPA privacy notes.

## What's been implemented (2026-02-XX — MVP)
- `/app/backend/{database,models,ip_resolver,matcher,scoring,summarizer,alerting,seed_data,server}.py`
- `/app/backend/target_accounts.csv` (20 seed accounts)
- `/app/frontend/public/index.html` (zero-framework dashboard)
- `/app/README.md` (setup, architecture, assumptions, GDPR/CCPA notes)
- Seed run: 200 events / 145 matched / 55 high-intent / 45 Claude alerts
- Testing agent: 9/9 backend pytests pass, all frontend testids +
  interactions verified.

## Prioritised backlog

### P1 — nice to have
- WebSocket / SSE instead of 5s polling for the alerts feed
- Per-account daily alert dedup / working-hours suppression
- Batch-summarise Claude calls (currently 1 request per alert)

### P2 — optional
- Charts (score-over-time per account, sparkline in table row)
- Auth (JWT) for real deployment
- Postgres migration guide with `alembic`
- Real Slack webhook + HubSpot property update wiring
- Retention job that hashes/rotates raw IPs after N days
