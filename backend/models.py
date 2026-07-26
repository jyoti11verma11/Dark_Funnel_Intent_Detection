"""SQLAlchemy ORM models for the Dark Funnel app."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, Index
from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    """Registered dashboard user (open signup)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(256), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class VisitorEvent(Base):
    """A single anonymous visitor pageview event."""
    __tablename__ = "visitor_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    ip_address = Column(String(64), index=True, nullable=False)
    page = Column(String(256), nullable=False)
    referrer = Column(String(256), nullable=True)
    user_agent = Column(String(512), nullable=True)
    session_duration_sec = Column(Integer, default=0)
    timestamp = Column(DateTime, default=_utcnow, index=True)

    # Denormalised resolved fields (filled by resolver on insert)
    resolved_company = Column(String(256), nullable=True, index=True)
    resolved_domain = Column(String(256), nullable=True, index=True)
    resolved_industry = Column(String(128), nullable=True)
    resolved_traffic_type = Column(String(32), nullable=True)  # business|residential|hosting

    # Match / score fields
    matched_account_id = Column(Integer, nullable=True, index=True)
    intent_score = Column(Float, default=0.0, index=True)


Index("ix_events_score_time", VisitorEvent.intent_score, VisitorEvent.timestamp)


class TargetAccount(Base):
    """CRM-managed list of target companies to watch for."""
    __tablename__ = "target_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_name = Column(String(256), nullable=False)
    domain = Column(String(256), unique=True, nullable=False, index=True)
    industry = Column(String(128), nullable=True)
    crm_owner = Column(String(128), nullable=True)
    tier = Column(String(32), default="B")  # A / B / C tier
    created_at = Column(DateTime, default=_utcnow)


class Alert(Base):
    """A fired alert for a high-intent event."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, nullable=False, index=True)
    account_id = Column(Integer, nullable=True)
    company_name = Column(String(256))
    crm_owner = Column(String(128))
    intent_score = Column(Float)
    summary = Column(Text, nullable=True)
    threshold = Column(Float)
    created_at = Column(DateTime, default=_utcnow, index=True)
    slack_sent = Column(Boolean, default=False)
    hubspot_updated = Column(Boolean, default=False)
