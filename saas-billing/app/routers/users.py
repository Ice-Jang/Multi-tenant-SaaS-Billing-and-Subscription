import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app import security
from app.crud import users as users_crud
from app.deps import AdminUser, CurrentUser, DbSession
from app.exceptions import AuthenticationError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.models import User, UserRole
from app.schemas import Page, UserCreate, UserRead, UserSelfUpdate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _assert_can_assign(actor: User, role: UserRole) -> None:
    if role in (UserRole.OWNER, UserRole.ADMIN) and actor.role != UserRole.OWNER:
        raise PermissionDeniedError("Only an owner can grant the owner or admin role")


def _assert_can_manage(actor: User, target: User) -> None:
    if actor.role != UserRole.OWNER and target.role != UserRole.MEMBER:
        raise PermissionDeniedError("Admins can only manage members")


def _get_or_404(db, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = users_crud.get(db, tenant_id, user_id)
    if user is None:  # same answer whether it doesn't exist or belongs to another tenant
        raise NotFoundError("User not found")
    return user


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, actor: AdminUser, db: DbSession):
    _assert_can_assign(actor, body.role)
    user = users_crud.create_user(
        db,
        actor.tenant_id,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
    )
    db.commit()
    return user


@router.get("", response_model=Page[UserRead])
def list_users(
    actor: AdminUser,
    db: DbSession,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    role: UserRole | None = None,
    is_active: bool | None = None,
):
    items, total = users_crud.list_users(
        db, actor.tenant_id, skip=skip, limit=limit, role=role, is_active=is_active
    )
    return Page[UserRead](
        items=[UserRead.model_validate(u) for u in items], total=total, skip=skip, limit=limit
    )


@router.get("/me", response_model=UserRead)
def read_me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserRead)
def update_me(body: UserSelfUpdate, user: CurrentUser, db: DbSession):
    changes: dict = {}
    if "full_name" in body.model_fields_set:
        changes["full_name"] = body.full_name
    if body.new_password is not None:
        if not security.verify_password(body.current_password or "", user.hashed_password):
            raise AuthenticationError("Current password is incorrect")
        changes["password"] = body.new_password
    users_crud.update_user(db, user, changes)
    db.commit()
    return user


@router.get("/{user_id}", response_model=UserRead)
def read_user(user_id: uuid.UUID, actor: AdminUser, db: DbSession):
    return _get_or_404(db, actor.tenant_id, user_id)


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: uuid.UUID, body: UserUpdate, actor: AdminUser, db: DbSession):
    target = _get_or_404(db, actor.tenant_id, user_id)
    changes = body.model_dump(exclude_unset=True)
    if target.id == actor.id and ({"role", "is_active"} & changes.keys()):
        raise BusinessRuleError("You cannot change your own role or active status.")
    _assert_can_manage(actor, target)
    if "role" in changes:
        _assert_can_assign(actor, changes["role"])
    users_crud.update_user(db, target, changes)
    db.commit()
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: uuid.UUID, actor: AdminUser, db: DbSession) -> Response:
    target = _get_or_404(db, actor.tenant_id, user_id)
    if target.id == actor.id:
        raise BusinessRuleError("You cannot delete your own account.")
    _assert_can_manage(actor, target)
    users_crud.delete_user(db, target)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
