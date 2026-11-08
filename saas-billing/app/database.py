"""SQLAlchemy engine and session factory (synchronous, psycopg 3)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=settings.db_echo,
)

# expire_on_commit=False lets us serialise ORM objects after commit without a re-SELECT.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables (dev/demo only; use Alembic migrations in production)."""
    from app.models import Base  # local import avoids a models <-> database cycle

    Base.metadata.create_all(bind=engine)
