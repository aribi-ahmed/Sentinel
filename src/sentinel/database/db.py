# src/sentinel/database/db.py
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sentinel.config.settings import settings
from sentinel.database.models import Base

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_URL = f"sqlite:///{BASE_DIR / 'sentinel_audit.db'}"

db_url = getattr(settings, "DATABASE_URL", None) or DEFAULT_DB_URL
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}

# 🛠️ Set echo=False to stop raw SQL logs from printing to terminal
engine = create_engine(db_url, echo=False, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Creates all tables defined in models.py if they don't exist yet."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for obtaining a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()