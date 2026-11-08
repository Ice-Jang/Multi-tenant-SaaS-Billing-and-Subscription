"""Tenant-isolation enforcement inside the SQLAlchemy session (defence in depth).

Layer 1 (crud/*):   every tenant-scoped CRUD function REQUIRES tenant_id and filters on it.
Layer 2 (here):     once a session is bound to a tenant, every ORM SELECT against a
                    TenantScopedMixin entity gets `WHERE tenant_id = :tenant` appended
                    automatically (also for joins, aliases and lazy loads), and every
                    INSERT/UPDATE/DELETE of a foreign tenant's row is rejected before flush.
Layer 3 (PostgreSQL): the transaction-local GUC `app.current_tenant` is set so the optional
                    Row-Level-Security policies in sql/rls.sql can enforce the same rule
                    inside the database itself.
"""
import uuid

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction, with_loader_criteria

from app.exceptions import TenantIsolationError
from app.models import TenantScopedMixin

TENANT_KEY = "tenant_id"
_GUC_SQL = text("SELECT set_config('app.current_tenant', :tid, true)")  # true = transaction-local


def set_tenant_context(db: Session, tenant_id: uuid.UUID) -> None:
    """Bind this session to one tenant. Call once per request, right after the tenant has been
    determined from a trusted source (verified JWT, or the tenant just created/looked up)."""
    db.info[TENANT_KEY] = tenant_id
    # Apply to the transaction that is already open (after_begin covers future ones).
    db.execute(_GUC_SQL, {"tid": str(tenant_id)})


def get_tenant_context(db: Session) -> uuid.UUID | None:
    return db.info.get(TENANT_KEY)


@event.listens_for(Session, "after_begin")
def _apply_guc_on_begin(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    """A commit ends the transaction (and the SET LOCAL). Re-apply for the next one."""
    tenant_id = session.info.get(TENANT_KEY)
    if tenant_id is not None:
        connection.execute(_GUC_SQL, {"tid": str(tenant_id)})


@event.listens_for(Session, "do_orm_execute")
def _scope_selects_to_tenant(state: ORMExecuteState) -> None:
    tenant_id = state.session.info.get(TENANT_KEY)
    if tenant_id is None or not state.is_select or state.is_column_load or state.is_relationship_load:
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, flush_context, instances) -> None:
    tenant_id = session.info.get(TENANT_KEY)
    if tenant_id is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantScopedMixin):
            if obj.tenant_id is None:
                obj.tenant_id = tenant_id  # safe default: stamp with the session's tenant
            elif obj.tenant_id != tenant_id:
                raise TenantIsolationError("Attempt to create a row for another tenant")
    for obj in (*session.dirty, *session.deleted):
        if isinstance(obj, TenantScopedMixin) and obj.tenant_id != tenant_id:
            raise TenantIsolationError("Attempt to modify or delete a row of another tenant")
