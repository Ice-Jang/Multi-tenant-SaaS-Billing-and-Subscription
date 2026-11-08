"""Subscription persistence + lifecycle rules. EVERY function is tenant-scoped."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crud._errors import translate_integrity_errors
from app.exceptions import BusinessRuleError, ConflictError
from app.models import PlanName, Subscription, SubscriptionStatus

LIVE_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.PAST_DUE,
)

# Allowed status transitions (state machine). Webhook/billing-job code should go through
# `transition_status` so illegal jumps (e.g. canceled -> active) are impossible.
ALLOWED_TRANSITIONS: dict[SubscriptionStatus, set[SubscriptionStatus]] = {
    SubscriptionStatus.TRIALING: {SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.ACTIVE: {SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.PAST_DUE: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELED},
    SubscriptionStatus.CANCELED: set(),
}


def get(db: Session, tenant_id: uuid.UUID, subscription_id: uuid.UUID) -> Subscription | None:
    return db.scalar(
        select(Subscription).where(
            Subscription.tenant_id == tenant_id, Subscription.id == subscription_id
        )
    )


def get_current(db: Session, tenant_id: uuid.UUID, *, for_update: bool = False) -> Subscription | None:
    """The tenant's single live subscription (guaranteed unique by a partial unique index)."""
    stmt = select(Subscription).where(
        Subscription.tenant_id == tenant_id, Subscription.status.in_(LIVE_STATUSES)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def list_subscriptions(
    db: Session, tenant_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> tuple[list[Subscription], int]:
    total = db.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.tenant_id == tenant_id)
    ) or 0
    rows = db.scalars(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return list(rows), total


def create_subscription(
    db: Session, tenant_id: uuid.UUID, *, plan_name: PlanName, trial_days: int = 0
) -> Subscription:
    if trial_days and plan_name == PlanName.FREE:
        raise BusinessRuleError("The free plan has no trial period.")
    if get_current(db, tenant_id) is not None:
        raise ConflictError("This tenant already has a live subscription.")

    now = datetime.now(UTC)
    if trial_days:
        trial_end = now + timedelta(days=trial_days)
        status, period_end = SubscriptionStatus.TRIALING, trial_end
    else:
        trial_end = None
        status = SubscriptionStatus.ACTIVE
        period_end = now + timedelta(days=get_settings().billing_period_days)

    sub = Subscription(
        tenant_id=tenant_id,
        plan_name=plan_name,
        status=status,
        current_period_start=now,
        current_period_end=period_end,
        trial_ends_at=trial_end,
    )
    db.add(sub)
    with translate_integrity_errors():  # the partial unique index catches concurrent creates
        db.flush()
    return sub


def transition_status(db: Session, sub: Subscription, new_status: SubscriptionStatus) -> Subscription:
    if new_status not in ALLOWED_TRANSITIONS[sub.status]:
        raise BusinessRuleError(f"Cannot move a subscription from '{sub.status}' to '{new_status}'.")
    sub.status = new_status
    if new_status == SubscriptionStatus.CANCELED:
        sub.canceled_at = datetime.now(UTC)
        sub.cancel_at_period_end = False
    db.flush()
    return sub


def change_plan(db: Session, sub: Subscription, plan_name: PlanName) -> Subscription:
    if sub.status == SubscriptionStatus.CANCELED:
        raise BusinessRuleError("A canceled subscription cannot change plan.")
    if sub.plan_name == plan_name:
        raise BusinessRuleError(f"The subscription is already on the '{plan_name}' plan.")
    sub.plan_name = plan_name
    db.flush()
    return sub


def cancel(db: Session, sub: Subscription, *, at_period_end: bool) -> Subscription:
    if sub.status == SubscriptionStatus.CANCELED:
        raise BusinessRuleError("The subscription is already canceled.")
    if at_period_end:
        sub.cancel_at_period_end = True
        db.flush()
        return sub
    return transition_status(db, sub, SubscriptionStatus.CANCELED)
