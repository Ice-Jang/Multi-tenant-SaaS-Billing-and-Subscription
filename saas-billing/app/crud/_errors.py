"""Translate PostgreSQL integrity violations into domain exceptions.

The database constraint is the source of truth (race-condition safe); the friendly message
is looked up by the constraint/index name that PostgreSQL reports."""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError

_MESSAGES = {
    "uq_tenants_slug": "This tenant slug is already taken.",
    "uq_tenants_domain": "This domain is already registered to another tenant.",
    "uq_users_tenant_id_email": "A user with this email already exists in this tenant.",
    "uq_subscriptions_tenant_live": "This tenant already has a live subscription.",
}


@contextmanager
def translate_integrity_errors() -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        name = getattr(diag, "constraint_name", None)
        raise ConflictError(_MESSAGES.get(name, "The change conflicts with existing data.")) from exc
