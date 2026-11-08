"""FastAPI dependencies: DB session, authentication, tenant binding and role checks."""
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import security
from app.crud import tenants as tenants_crud
from app.crud import users as users_crud
from app.database import SessionLocal
from app.exceptions import AuthenticationError, PermissionDeniedError
from app.models import User, UserRole
from app.tenancy import set_tenant_context

_bearer = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")


def get_db() -> Iterator[Session]:
    """One session per request; closing it rolls back anything not explicitly committed."""
    with SessionLocal() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Authenticate the caller AND bind the DB session to the tenant in the verified token.
    The tenant is never taken from a header, path or body, so a client cannot choose it."""
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    user_id, tenant_id = security.decode_access_token(credentials.credentials)

    set_tenant_context(db, tenant_id)  # from here on, all queries are tenant-filtered

    tenant = tenants_crud.get_by_id(db, tenant_id)
    if tenant is None or not tenant.is_active:
        raise AuthenticationError("Tenant is inactive")
    user = users_crud.get(db, tenant_id, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User is inactive or no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole):
    def _checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionDeniedError("Your role does not allow this action")
        return user

    return _checker


OwnerUser = Annotated[User, Depends(require_roles(UserRole.OWNER))]
AdminUser = Annotated[User, Depends(require_roles(UserRole.OWNER, UserRole.ADMIN))]
