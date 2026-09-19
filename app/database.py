from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings
from urllib.parse import quote_plus

def _build_sqlalchemy_database_url() -> str:
    if settings.database_url:
        url = settings.database_url
    else:
        missing = [
            key
            for key, value in {
                "database_username": settings.database_username,
                "database_password": settings.database_password,
                "database_hostname": settings.database_hostname,
                "database_port": settings.database_port,
                "database_name": settings.database_name,
            }.items()
            if not value
        ]
        if missing:
            # Fallback to local sqlite for development and test environments if DB vars are unset
            return "sqlite:///./voteflow.db"

        url = (
            f"postgresql://{settings.database_username}:{quote_plus(settings.database_password)}"
            f"@{settings.database_hostname}:{settings.database_port}/{settings.database_name}"
        )

    # Render and some providers use postgres://, SQLAlchemy expects postgresql://.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


SQLALCHEMY_DATABASE_URL = _build_sqlalchemy_database_url()

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()