"""
Dark Funnel Intent Detection - FastAPI backend.

All API routes are prefixed with /api. Data is stored in SQLite via SQLAlchemy;
swap DATABASE_URL to Postgres to migrate.
"""
from __future__ import annotations

import csv
import io
import os
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from database import SessionLocal, init_db
from models import Alert, TargetAccount, User, VisitorEvent
from ip_resolver import resolve_ip
from matcher import match_domain, _normalise_domain
from scoring import compute_intent_score
from summarizer import summarize_session
from alerting import send_to_slack, update_hubspot_property
from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("dark_funnel")

INTENT_ALERT_THRESHOLD = int(os.environ.get("INTENT_ALERT_THRESHOLD", "70"))

# ---------------------------------------------------------------------------
# App & DB setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Dark Funnel Intent Detection")
api = APIRouter(prefix="/api")

# Everything under /api requires auth EXCEPT the auth router itself and /api/.
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
protected = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


@app.on_event("startup")
def _startup():
    init_db()
    logger.info("SQLite database ready.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class EventIn(BaseModel):
    ip_address: str
    page: str
    session_id: Optional[str] = None
    referrer: Optional[str] = None
    user_agent: Optional[str] = None
    session_duration_sec: int = 0
    timestamp: Optional[datetime] = None


class EventOut(BaseModel):
    id: int
    session_id: str
    ip_address: str
    page: str
    referrer: Optional[str]
    session_duration_sec: int
    timestamp: datetime
    resolved_company: Optional[str]
    resolved_domain: Optional[str]
    resolved_industry: Optional[str]
    resolved_traffic_type: Optional[str]
    matched_account_id: Optional[int]
    intent_score: float

    class Config:
        from_attributes = True


class TargetAccountIn(BaseModel):
    company_name: str
    domain: str
    industry: Optional[str] = None
    crm_owner: Optional[str] = None
    tier: str = "B"


class TargetAccountOut(TargetAccountIn):
    id: int

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    id: int
    event_id: int
    account_id: Optional[int]
    company_name: Optional[str]
    crm_owner: Optional[str]
    intent_score: float
    summary: Optional[str]
    threshold: float
    created_at: datetime
    slack_sent: bool
    hubspot_updated: bool

    class Config:
        from_attributes = True


class MatchedVisitOut(BaseModel):
    account_id: int
    company_name: str
    domain: str
    industry: Optional[str]
    crm_owner: Optional[str]
    tier: str
    pages_viewed: List[str]
    visit_count: int
    max_score: float
    total_duration_sec: int
    last_seen: datetime
    latest_event_id: int
    latest_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Health (public)
# ---------------------------------------------------------------------------
@api.get("/")
def root():
    return {
        "app": "Dark Funnel Intent Detection",
        "status": "ok",
        "alert_threshold": INTENT_ALERT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class SignupIn(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginIn(BaseModel):
    email: str
    password: str


class AuthOut(BaseModel):
    token: str
    user: dict


def _user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "created_at": u.created_at.isoformat() if u.created_at else None}


@auth_router.post("/signup", response_model=AuthOut)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required.")
    if not payload.password or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(payload.password), name=(payload.name or "").strip() or None)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.email)
    return {"token": token, "user": _user_dict(user)}


@auth_router.post("/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    email = (payload.email or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password or "", user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_access_token(user.id, user.email)
    return {"token": token, "user": _user_dict(user)}


@auth_router.get("/me")
def me(current: User = Depends(get_current_user)):
    return _user_dict(current)


# ---------------------------------------------------------------------------
# Events (ingest + list)
# ---------------------------------------------------------------------------
@protected.post("/events", response_model=EventOut)
async def ingest_event(payload: EventIn, db: Session = Depends(get_db)):
    now = payload.timestamp or datetime.now(timezone.utc)
    resolved = resolve_ip(payload.ip_address)
    matched = match_domain(db, resolved.get("domain"))
    tier = matched.tier if matched else None

    prior_timestamps: list[datetime] = []
    if matched is not None:
        prior = (
            db.query(VisitorEvent.timestamp)
            .filter(VisitorEvent.matched_account_id == matched.id)
            .filter(VisitorEvent.timestamp < now)
            .filter(VisitorEvent.timestamp >= now - timedelta(hours=48))
            .all()
        )
        prior_timestamps = [p[0] for p in prior]

    score = compute_intent_score(
        payload.page,
        payload.session_duration_sec,
        now,
        prior_timestamps,
        tier=tier,
    )

    event = VisitorEvent(
        session_id=payload.session_id or uuid.uuid4().hex[:16],
        ip_address=payload.ip_address,
        page=payload.page,
        referrer=payload.referrer,
        user_agent=payload.user_agent,
        session_duration_sec=payload.session_duration_sec,
        timestamp=now,
        resolved_company=resolved.get("company"),
        resolved_domain=resolved.get("domain"),
        resolved_industry=resolved.get("industry"),
        resolved_traffic_type=resolved.get("traffic_type"),
        matched_account_id=matched.id if matched else None,
        intent_score=float(score),
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Fire alert if crossed threshold and matched a target account.
    if matched is not None and score >= INTENT_ALERT_THRESHOLD:
        await _fire_alert(db, event, matched)

    return event


@protected.get("/events", response_model=List[EventOut])
def list_events(
    limit: int = 100,
    high_intent_only: bool = False,
    matched_only: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(VisitorEvent)
    if high_intent_only:
        q = q.filter(VisitorEvent.intent_score >= INTENT_ALERT_THRESHOLD)
    if matched_only:
        q = q.filter(VisitorEvent.matched_account_id.isnot(None))
    events = q.order_by(desc(VisitorEvent.timestamp)).limit(limit).all()
    return events


# ---------------------------------------------------------------------------
# Matched visits (aggregated per-company view used by the dashboard)
# ---------------------------------------------------------------------------
@protected.get("/visits/matched", response_model=List[MatchedVisitOut])
def matched_visits(
    high_intent_only: bool = False,
    db: Session = Depends(get_db),
):
    events = (
        db.query(VisitorEvent)
        .filter(VisitorEvent.matched_account_id.isnot(None))
        .order_by(desc(VisitorEvent.timestamp))
        .all()
    )
    if not events:
        return []

    accounts = {a.id: a for a in db.query(TargetAccount).all()}

    # Group by account
    grouped: dict[int, list[VisitorEvent]] = {}
    for ev in events:
        grouped.setdefault(ev.matched_account_id, []).append(ev)

    # Latest alert summary per account
    latest_alerts = {}
    for a in db.query(Alert).order_by(desc(Alert.created_at)).all():
        latest_alerts.setdefault(a.account_id, a.summary)

    results: List[MatchedVisitOut] = []
    for account_id, evs in grouped.items():
        acc = accounts.get(account_id)
        if acc is None:
            continue
        max_score = max(e.intent_score for e in evs)
        if high_intent_only and max_score < INTENT_ALERT_THRESHOLD:
            continue
        pages_ordered = list(dict.fromkeys(e.page for e in evs))
        total_dur = sum(e.session_duration_sec or 0 for e in evs)
        last_seen = max(e.timestamp for e in evs)
        latest_ev = max(evs, key=lambda e: e.timestamp)
        results.append(
            MatchedVisitOut(
                account_id=acc.id,
                company_name=acc.company_name,
                domain=acc.domain,
                industry=acc.industry,
                crm_owner=acc.crm_owner,
                tier=acc.tier,
                pages_viewed=pages_ordered,
                visit_count=len(evs),
                max_score=max_score,
                total_duration_sec=total_dur,
                last_seen=last_seen,
                latest_event_id=latest_ev.id,
                latest_summary=latest_alerts.get(acc.id),
            )
        )
    results.sort(key=lambda r: r.max_score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Target accounts CRUD + CSV import
# ---------------------------------------------------------------------------
@protected.get("/accounts", response_model=List[TargetAccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(TargetAccount).order_by(TargetAccount.company_name).all()


@protected.post("/accounts", response_model=TargetAccountOut)
def create_account(payload: TargetAccountIn, db: Session = Depends(get_db)):
    domain = _normalise_domain(payload.domain)
    existing = db.query(TargetAccount).filter(TargetAccount.domain == domain).first()
    if existing:
        raise HTTPException(status_code=409, detail="Account with this domain already exists.")
    acc = TargetAccount(
        company_name=payload.company_name,
        domain=domain,
        industry=payload.industry,
        crm_owner=payload.crm_owner,
        tier=payload.tier or "B",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


@protected.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.query(TargetAccount).filter(TargetAccount.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(acc)
    db.commit()
    return {"deleted": account_id}


@protected.post("/accounts/import")
async def import_accounts(file: UploadFile = File(...), db: Session = Depends(get_db)):
    text = (await file.read()).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    added = 0
    skipped = 0
    for row in reader:
        domain = _normalise_domain(row.get("domain", ""))
        if not domain:
            skipped += 1
            continue
        if db.query(TargetAccount).filter(TargetAccount.domain == domain).first():
            skipped += 1
            continue
        db.add(TargetAccount(
            company_name=row.get("company_name", domain),
            domain=domain,
            industry=row.get("industry"),
            crm_owner=row.get("crm_owner"),
            tier=(row.get("tier") or "B").upper(),
        ))
        added += 1
    db.commit()
    return {"added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
async def _fire_alert(db: Session, event: VisitorEvent, account: TargetAccount) -> Alert:
    """Create an Alert row, generate a summary, and fire stubbed integrations."""
    # Build session context: all events for this account in the last 48h.
    since = event.timestamp - timedelta(hours=48)
    session_events = (
        db.query(VisitorEvent)
        .filter(VisitorEvent.matched_account_id == account.id)
        .filter(VisitorEvent.timestamp >= since)
        .order_by(VisitorEvent.timestamp)
        .all()
    )
    pages = [e.page for e in session_events] or [event.page]
    total_dur = sum(e.session_duration_sec or 0 for e in session_events) or event.session_duration_sec

    summary = await summarize_session(
        company=account.company_name,
        pages=pages,
        total_duration_sec=total_dur,
        visits=len(session_events) or 1,
        industry=account.industry,
    )

    alert = Alert(
        event_id=event.id,
        account_id=account.id,
        company_name=account.company_name,
        crm_owner=account.crm_owner,
        intent_score=event.intent_score,
        summary=summary,
        threshold=INTENT_ALERT_THRESHOLD,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    payload = {
        "company": account.company_name,
        "crm_owner": account.crm_owner,
        "intent_score": event.intent_score,
        "summary": summary,
        "page": event.page,
        "account_id": account.id,
    }
    try:
        alert.slack_sent = bool(send_to_slack(payload))
        alert.hubspot_updated = bool(update_hubspot_property(payload))
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("stubbed integration failed: %s", e)
    return alert


@protected.get("/alerts", response_model=List[AlertOut])
def list_alerts(limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(Alert)
        .order_by(desc(Alert.created_at))
        .limit(limit)
        .all()
    )


@protected.post("/alerts/regenerate")
async def regenerate_alerts(db: Session = Depends(get_db)):
    """
    Sweep existing high-intent matched events and fire alerts for any that
    haven't produced one yet. Useful after running the seed script.
    """
    # Existing alerted event_ids
    existing_event_ids = {a.event_id for a in db.query(Alert.event_id).all()}
    events = (
        db.query(VisitorEvent)
        .filter(VisitorEvent.intent_score >= INTENT_ALERT_THRESHOLD)
        .filter(VisitorEvent.matched_account_id.isnot(None))
        .order_by(VisitorEvent.timestamp)
        .all()
    )
    accounts = {a.id: a for a in db.query(TargetAccount).all()}
    fired = 0
    for ev in events:
        if ev.id in existing_event_ids:
            continue
        acc = accounts.get(ev.matched_account_id)
        if acc is None:
            continue
        await _fire_alert(db, ev, acc)
        fired += 1
    return {"fired": fired, "total_high_intent": len(events)}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@protected.get("/stats")
def stats(db: Session = Depends(get_db)):
    total = db.query(VisitorEvent).count()
    matched = db.query(VisitorEvent).filter(VisitorEvent.matched_account_id.isnot(None)).count()
    high = db.query(VisitorEvent).filter(VisitorEvent.intent_score >= INTENT_ALERT_THRESHOLD).count()
    alerts = db.query(Alert).count()
    accounts = db.query(TargetAccount).count()
    return {
        "total_events": total,
        "matched_events": matched,
        "high_intent_events": high,
        "alerts": alerts,
        "target_accounts": accounts,
        "alert_threshold": INTENT_ALERT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Wire up
# ---------------------------------------------------------------------------
app.include_router(api)
app.include_router(auth_router)
app.include_router(protected)

origins = os.environ.get(
    "CORS_ORIGINS",
    "https://dark-funnel-intent-detection-1.onrender.com"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
