"""SQLAlchemy 2.0 ORM models.

Tenant isolation at the schema level:
  * `Tenant` is the isolation root.
  * Every other core table inherits `TenantScopedMixin`, which adds a NOT NULL
    `tenant_id` FK -> tenants.id (ON DELETE CASCADE) with an index.
  * Uniqueness that is "per tenant" is always a composite constraint starting with tenant_id.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class PlanName(enum.StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(enum.StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


def _str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint (easier to migrate than native PG enums), lowercase values."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=20,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


# --------------------------------------------------------------------------- mixins
class TimestampMixin:
    # eager_defaults: fetch server-generated values (created_at/updated_at) via RETURNING.
    __mapper_args__ = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantScopedMixin:
    """Marks a table as tenant-owned. app/tenancy.py keys its automatic query filter and
    write guard off this class, so any new model that inherits it is protected for free."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


# --------------------------------------------------------------------------- models
class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="slug_format"),
    )

    # PostgreSQL 18 native time-ordered UUIDv7: index-friendly and non-enumerable.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    domain: Mapped[str | None] = mapped_column(String(253), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class User(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Email is unique *per tenant*, so the same person can belong to several tenants.
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
        Index("ix_users_tenant_id_role", "tenant_id", "role"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    email: Mapped[str] = mapped_column(String(320))
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        _str_enum(UserRole, "role"), default=UserRole.MEMBER, server_default=UserRole.MEMBER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    tenant: Mapped["Tenant"] = relationship(back_populates="users", lazy="raise")


class Subscription(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("current_period_end > current_period_start", name="period_valid"),
        Index("ix_subscriptions_tenant_id_status", "tenant_id", "status"),
        # Business invariant enforced by the database: at most ONE live subscription per tenant.
        Index(
            "uq_subscriptions_tenant_live",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'trialing', 'past_due')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    plan_name: Mapped[PlanName] = mapped_column(
        _str_enum(PlanName, "plan_name"), default=PlanName.FREE, server_default=PlanName.FREE.value
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        _str_enum(SubscriptionStatus, "status"),
        default=SubscriptionStatus.ACTIVE,
        server_default=SubscriptionStatus.ACTIVE.value,
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped["Tenant"] = relationship(back_populates="subscriptions", lazy="raise")
