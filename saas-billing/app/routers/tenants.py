from fastapi import APIRouter, Response, status

from app.crud import subscriptions as subscriptions_crud
from app.crud import tenants as tenants_crud
from app.crud import users as users_crud
from app.deps import AdminUser, CurrentUser, DbSession, OwnerUser
from app.exceptions import NotFoundError
from app.models import PlanName, UserRole
from app.schemas import TenantRead, TenantSignup, TenantSignupResponse, TenantUpdate
from app.tenancy import set_tenant_context

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantSignupResponse, status_code=status.HTTP_201_CREATED)
def signup(body: TenantSignup, db: DbSession) -> TenantSignupResponse:
    """Public onboarding. Tenant, owner and free subscription are created in ONE transaction:
    if any step fails (e.g. duplicate slug) nothing is persisted."""
    tenant = tenants_crud.create_tenant(db, name=body.tenant_name, slug=body.slug, domain=body.domain)
    set_tenant_context(db, tenant.id)
    owner = users_crud.create_user(
        db,
        tenant.id,
        email=body.owner_email,
        password=body.owner_password,
        full_name=body.owner_full_name,
        role=UserRole.OWNER,
    )
    subscription = subscriptions_crud.create_subscription(db, tenant.id, plan_name=PlanName.FREE)
    db.commit()
    return TenantSignupResponse(
        tenant=TenantRead.model_validate(tenant),
        owner=owner,  # type: ignore[arg-type]  # validated from ORM by FastAPI
        subscription=subscription,  # type: ignore[arg-type]
    )


@router.get("/me", response_model=TenantRead)
def get_my_tenant(user: CurrentUser, db: DbSession):
    tenant = tenants_crud.get_by_id(db, user.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return tenant


@router.patch("/me", response_model=TenantRead)
def update_my_tenant(body: TenantUpdate, user: AdminUser, db: DbSession):
    tenant = tenants_crud.get_by_id(db, user.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    tenants_crud.update_tenant(db, tenant, body.model_dump(exclude_unset=True))
    db.commit()
    return tenant


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_my_tenant(user: OwnerUser, db: DbSession) -> Response:
    """Soft-delete: the tenant and all its data are kept but nobody can log in."""
    tenant = tenants_crud.get_by_id(db, user.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    tenants_crud.deactivate_tenant(db, tenant)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
