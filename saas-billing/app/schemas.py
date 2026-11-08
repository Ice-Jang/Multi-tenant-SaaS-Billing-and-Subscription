"""Pydantic v2 request/response models."""
import re
import uuid
from datetime import datetime
from typing import Annotated, ClassVar, Generic, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models import PlanName, SubscriptionStatus, UserRole

# --------------------------------------------------------------------------- shared types
NormalizedEmail = Annotated[EmailStr, AfterValidator(lambda v: v.lower())]
Password = Annotated[str, StringConstraints(min_length=12, max_length=128)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
TenantName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=255)]
Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=3, max_length=63,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
    ),
]
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _validate_domain(v: str | None) -> str | None:
    if v is None:
        return v
    v = v.strip().lower()
    if not _DOMAIN_RE.match(v):
        raise ValueError("Invalid domain name")
    return v


Domain = Annotated[str, AfterValidator(_validate_domain)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PatchModel(BaseModel):
    """Base for PATCH bodies: explicit `null` is rejected except for `nullable_fields`."""

    nullable_fields: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def _reject_explicit_nulls(self):
        for name in self.model_fields_set:
            if getattr(self, name) is None and name not in self.nullable_fields:
                raise ValueError(f"'{name}' cannot be null")
        return self


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    skip: int
    limit: int


# --------------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    tenant_slug: Slug
    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# --------------------------------------------------------------------------- tenants
class TenantRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    domain: str | None
    is_active: bool
    created_at: datetime


class TenantUpdate(PatchModel):
    nullable_fields: ClassVar[set[str]] = {"domain"}

    name: TenantName | None = None
    domain: Domain | None = None


# --------------------------------------------------------------------------- users
class UserRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    email: NormalizedEmail
    password: Password
    full_name: FullName
    role: UserRole = UserRole.MEMBER


class UserUpdate(PatchModel):
    full_name: FullName | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: Password | None = None


class UserSelfUpdate(PatchModel):
    full_name: FullName | None = None
    current_password: str | None = Field(default=None, max_length=128)
    new_password: Password | None = None

    @model_validator(mode="after")
    def _password_change_needs_current(self):
        if (self.new_password is None) != (self.current_password is None):
            raise ValueError("current_password and new_password must be provided together")
        return self


# --------------------------------------------------------------------------- subscriptions
class SubscriptionRead(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_name: PlanName
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    canceled_at: datetime | None
    created_at: datetime


class SubscriptionCreate(BaseModel):
    plan_name: PlanName
    trial_days: int = Field(default=0, ge=0, le=30)


class ChangePlanRequest(BaseModel):
    plan_name: PlanName


class CancelRequest(BaseModel):
    at_period_end: bool = True


# --------------------------------------------------------------------------- onboarding
class TenantSignup(BaseModel):
    """Public self-service onboarding: creates tenant + owner + free subscription atomically."""

    tenant_name: TenantName
    slug: Slug
    domain: Domain | None = None
    owner_email: NormalizedEmail
    owner_password: Password
    owner_full_name: FullName

    @field_validator("domain", mode="before")
    @classmethod
    def _blank_domain_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class TenantSignupResponse(BaseModel):
    tenant: TenantRead
    owner: UserRead
    subscription: SubscriptionRead
