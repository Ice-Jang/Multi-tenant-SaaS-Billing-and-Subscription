from typing import Annotated

from fastapi import APIRouter, Query, status

from app.crud import subscriptions as subscriptions_crud
from app.deps import AdminUser, CurrentUser, DbSession, OwnerUser
from app.exceptions import NotFoundError
from app.schemas import (
    CancelRequest,
    ChangePlanRequest,
    Page,
    SubscriptionCreate,
    SubscriptionRead,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/current", response_model=SubscriptionRead)
def get_current_subscription(user: CurrentUser, db: DbSession):
    sub = subscriptions_crud.get_current(db, user.tenant_id)
    if sub is None:
        raise NotFoundError("This tenant has no live subscription")
    return sub


@router.get("", response_model=Page[SubscriptionRead])
def list_subscriptions(
    user: AdminUser,
    db: DbSession,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    items, total = subscriptions_crud.list_subscriptions(db, user.tenant_id, skip=skip, limit=limit)
    return Page[SubscriptionRead](
        items=[SubscriptionRead.model_validate(s) for s in items], total=total, skip=skip, limit=limit
    )


@router.post("", response_model=SubscriptionRead, status_code=status.HTTP_201_CREATED)
def create_subscription(body: SubscriptionCreate, user: OwnerUser, db: DbSession):
    """Start a new subscription (only possible when the previous one has been canceled)."""
    sub = subscriptions_crud.create_subscription(
        db, user.tenant_id, plan_name=body.plan_name, trial_days=body.trial_days
    )
    db.commit()
    return sub


@router.post("/current/change-plan", response_model=SubscriptionRead)
def change_plan(body: ChangePlanRequest, user: OwnerUser, db: DbSession):
    sub = subscriptions_crud.get_current(db, user.tenant_id, for_update=True)
    if sub is None:
        raise NotFoundError("This tenant has no live subscription")
    subscriptions_crud.change_plan(db, sub, body.plan_name)
    db.commit()
    return sub


@router.post("/current/cancel", response_model=SubscriptionRead)
def cancel_subscription(body: CancelRequest, user: OwnerUser, db: DbSession):
    sub = subscriptions_crud.get_current(db, user.tenant_id, for_update=True)
    if sub is None:
        raise NotFoundError("This tenant has no live subscription")
    subscriptions_crud.cancel(db, sub, at_period_end=body.at_period_end)
    db.commit()
    return sub
