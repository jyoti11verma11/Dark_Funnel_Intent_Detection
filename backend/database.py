"""SQLAlchemy database setup. SQLite by default; swap DATABASE_URL to Postgres later."""
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////app/backend/dark_funnel.db")

# check_same_thread is only relevant for SQLite; ignored by other drivers.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # Import models so metadata is registered
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
