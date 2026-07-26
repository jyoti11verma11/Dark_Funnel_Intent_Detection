"""
Synthetic visitor event generator.

Creates ~200 visitor events across ~15 fake companies over the past 14 days,
including some repeat visitors. Also loads the target-account CSV on first run.

Usage (from /app):
    python -m backend.seed_data
or:
    python backend/seed_data.py
"""
from __future__ import annotations

import csv
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow both `python -m seed_data` (from backend/) and `python backend/seed_data.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from database import SessionLocal, init_db  # noqa: E402
from models import VisitorEvent, TargetAccount  # noqa: E402
from ip_resolver import MOCK_COMPANY_SUBNETS, resolve_ip  # noqa: E402
from matcher import match_domain  # noqa: E402
from scoring import compute_intent_score  # noqa: E402


PAGES = [
    "/",
    "/pricing",
    "/demo",
    "/contact-sales",
    "/integrations",
    "/product",
    "/features",
    "/docs",
    "/case-studies/acme-migration",
    "/blog/why-buyers-hide",
    "/blog/intent-signals",
    "/enterprise",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 Chrome/121 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1",
]

REFERRERS = [
    None,
    "https://google.com/",
    "https://linkedin.com/",
    "https://news.ycombinator.com/",
    "https://twitter.com/",
    "https://duckduckgo.com/",
    "direct",
]


TARGET_CSV = Path(__file__).parent / "target_accounts.csv"


def load_targets(db) -> int:
    if db.query(TargetAccount).count() > 0:
        return db.query(TargetAccount).count()
    with open(TARGET_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            db.add(TargetAccount(
                company_name=row["company_name"],
                domain=row["domain"].strip().lower(),
                industry=row.get("industry"),
                crm_owner=row.get("crm_owner"),
                tier=row.get("tier") or "B",
            ))
        db.commit()
    return db.query(TargetAccount).count()


def _random_ip_from_subnet(prefix: str) -> str:
    return f"{prefix}.{random.randint(1, 250)}"


def _random_residential_ip() -> str:
    first = random.choice([24, 76, 98, 172, 192])
    if first == 172:
        return f"172.{random.randint(16, 31)}.{random.randint(0, 255)}.{random.randint(1, 250)}"
    if first == 192:
        return f"192.168.{random.randint(0, 255)}.{random.randint(1, 250)}"
    return f"{first}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 250)}"


def generate_events(db, n_events: int = 200, days_back: int = 14) -> int:
    random.seed(42)  # deterministic seed
    now = datetime.now(timezone.utc)

    # Pick 15 subnets from our known mock ranges (matched companies)
    business_subnets = [
        p for p, v in MOCK_COMPANY_SUBNETS.items()
        if v.get("traffic_type") == "business"
    ]
    random.shuffle(business_subnets)
    active_subnets = business_subnets[:15]

    # ~70% events from target companies, 20% residential, 10% hosting/unknown
    created = 0
    # session grouping: reuse session_ids to create multi-page sessions
    active_sessions: list[tuple[str, str, datetime]] = []  # (session_id, ip, ts)

    for _ in range(n_events):
        roll = random.random()
        if roll < 0.70:
            ip = _random_ip_from_subnet(random.choice(active_subnets))
        elif roll < 0.90:
            ip = _random_residential_ip()
        else:
            # hosting
            ip = _random_ip_from_subnet(random.choice(["52.200.10", "34.120.5"]))

        # 30% chance this event continues an existing session (same ip, same sid)
        continued = False
        if active_sessions and random.random() < 0.30:
            for sid, sip, sts in active_sessions[-20:]:
                if sip == ip and (now - sts).total_seconds() < 60 * 60:
                    session_id = sid
                    ts = sts + timedelta(seconds=random.randint(10, 120))
                    continued = True
                    break
        if not continued:
            session_id = uuid.uuid4().hex[:16]
            # Weight timestamps toward more recent days.
            day_offset = int(random.triangular(0, days_back, 2))
            ts = now - timedelta(
                days=day_offset,
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            )

        page = random.choice(PAGES)
        # Duration: pricing/demo pages skew longer.
        if any(k in page for k in ("/pricing", "/demo", "/integrations", "/enterprise")):
            duration = random.randint(60, 480)
        elif "/blog" in page:
            duration = random.randint(15, 120)
        else:
            duration = random.randint(5, 180)

        resolved = resolve_ip(ip)
        matched = match_domain(db, resolved.get("domain"))

        # Repeat detection: any prior events for this company in last 48h.
        prior_timestamps: list[datetime] = []
        if matched is not None:
            prior = (
                db.query(VisitorEvent.timestamp)
                .filter(VisitorEvent.matched_account_id == matched.id)
                .filter(VisitorEvent.timestamp < ts)
                .filter(VisitorEvent.timestamp >= ts - timedelta(hours=48))
                .all()
            )
            prior_timestamps = [p[0] for p in prior]

        tier = matched.tier if matched else None
        score = compute_intent_score(page, duration, ts, prior_timestamps, tier=tier)

        event = VisitorEvent(
            session_id=session_id,
            ip_address=ip,
            page=page,
            referrer=random.choice(REFERRERS),
            user_agent=random.choice(USER_AGENTS),
            session_duration_sec=duration,
            timestamp=ts,
            resolved_company=resolved.get("company"),
            resolved_domain=resolved.get("domain"),
            resolved_industry=resolved.get("industry"),
            resolved_traffic_type=resolved.get("traffic_type"),
            matched_account_id=matched.id if matched else None,
            intent_score=float(score),
        )
        db.add(event)
        active_sessions.append((session_id, ip, ts))
        created += 1

    db.commit()
    return created


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        n_targets = load_targets(db)
        print(f"[seed] target accounts in DB: {n_targets}")

        # Clear any pre-existing events for reproducibility
        db.query(VisitorEvent).delete()
        db.commit()

        n = generate_events(db)
        print(f"[seed] created {n} visitor events")

        matched = db.query(VisitorEvent).filter(VisitorEvent.matched_account_id.isnot(None)).count()
        high = db.query(VisitorEvent).filter(VisitorEvent.intent_score >= 70).count()
        print(f"[seed] matched to target accounts: {matched}")
        print(f"[seed] high-intent events (score >= 70): {high}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
