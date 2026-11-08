"""User persistence. EVERY function takes an explicit tenant_id and filters on it."""
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security
from app.crud._errors import translate_integrity_errors
from app.exceptions import BusinessRuleError
from app.models import User, UserRole


def get(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    return db.scalar(select(User).where(User.tenant_id == tenant_id, User.id == user_id))


def get_by_email(db: Session, tenant_id: uuid.UUID, email: str) -> User | None:
    return db.scalar(select(User).where(User.tenant_id == tenant_id, User.email == email.lower()))


def list_users(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 50,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> tuple[list[User], int]:
    filters = [User.tenant_id == tenant_id]
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    rows = db.scalars(
        select(User).where(*filters).order_by(User.id).offset(skip).limit(limit)
    ).all()
    return list(rows), total


def create_user(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    email: str,
    password: str,
    full_name: str,
    role: UserRole = UserRole.MEMBER,
) -> User:
    user = User(
        tenant_id=tenant_id,
        email=email.lower(),
        hashed_password=security.hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    with translate_integrity_errors():
        db.flush()
    return user


def _ensure_not_last_owner(db: Session, user: User) -> None:
    """A tenant must always keep one active owner. The owner rows are locked (FOR UPDATE) so two
    concurrent demotions cannot both pass this check."""
    if user.role != UserRole.OWNER or not user.is_active:
        return
    owner_ids = set(
        db.scalars(
            select(User.id)
            .where(
                User.tenant_id == user.tenant_id,
                User.role == UserRole.OWNER,
                User.is_active.is_(True),
            )
            .with_for_update()
        )
    )
    if owner_ids <= {user.id}:
        raise BusinessRuleError("A tenant must keep at least one active owner.")


def update_user(db: Session, user: User, changes: dict[str, Any]) -> User:
    loses_owner = ("role" in changes and changes["role"] != UserRole.OWNER) or (
        changes.get("is_active") is False
    )
    if loses_owner:
        _ensure_not_last_owner(db, user)
    if "full_name" in changes:
        user.full_name = changes["full_name"]
    if "role" in changes:
        user.role = changes["role"]
    if "is_active" in changes:
        user.is_active = changes["is_active"]
    if "password" in changes:
        user.hashed_password = security.hash_password(changes["password"])
    db.flush()
    return user


def delete_user(db: Session, user: User) -> None:
    _ensure_not_last_owner(db, user)
    db.delete(user)
    db.flush()


def authenticate(db: Session, tenant_id: uuid.UUID, email: str, password: str) -> User | None:
    user = get_by_email(db, tenant_id, email)
    if user is None:
        security.verify_password(password, security.DUMMY_HASH)  # equalise timing
        return None
    if not security.verify_password(password, user.hashed_password) or not user.is_active:
        return None
    if security.needs_rehash(user.hashed_password):  # transparently upgrade hash parameters
        user.hashed_password = security.hash_password(password)
        db.flush()
    return user
