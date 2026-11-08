from fastapi import APIRouter

from app import security
from app.crud import tenants as tenants_crud
from app.crud import users as users_crud
from app.deps import DbSession
from app.exceptions import AuthenticationError
from app.schemas import LoginRequest, TokenResponse
from app.tenancy import set_tenant_context

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    """Email is unique per tenant, so login identifies the tenant by its slug."""
    tenant = tenants_crud.get_by_slug(db, body.tenant_slug)
    if tenant is None or not tenant.is_active:
        security.verify_password(body.password, security.DUMMY_HASH)  # equalise timing
        raise AuthenticationError("Invalid credentials")

    set_tenant_context(db, tenant.id)
    user = users_crud.authenticate(db, tenant.id, body.email, body.password)
    if user is None:
        raise AuthenticationError("Invalid credentials")
    db.commit()  # persists a possible password-hash upgrade

    token, expires_in = security.create_access_token(user_id=user.id, tenant_id=tenant.id)
    return TokenResponse(access_token=token, expires_in=expires_in)
