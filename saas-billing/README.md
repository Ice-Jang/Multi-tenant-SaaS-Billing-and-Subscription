# Multi-tenant SaaS Billing & Subscription Engine

FastAPI + SQLAlchemy 2.0 + PostgreSQL 18 (shared database, `tenant_id` isolation).

## Run
```bash
cp .env.example .env            # set SECRET_KEY
docker compose up -d db         # PostgreSQL 18
pip install -r requirements.txt
uvicorn app.main:app --reload   # docs: http://localhost:8000/docs
pytest -q                       # needs requirements-dev.txt + a test DB (see tests/test_api.py)
```

## Tenant isolation (defence in depth)
1. **Schema** – every tenant-owned table inherits `TenantScopedMixin` (`tenant_id` NOT NULL, FK -> tenants ON DELETE CASCADE, indexed). Per-tenant uniqueness is composite (`UNIQUE (tenant_id, email)`); one live subscription per tenant is a partial unique index.
2. **CRUD layer** – every function takes a mandatory `tenant_id` and filters on it.
3. **Session layer (`app/tenancy.py`)** – once bound to a tenant: `do_orm_execute` + `with_loader_criteria(TenantScopedMixin, ...)` appends `tenant_id = :tenant` to every ORM SELECT (joins, aliases, lazy loads); `before_flush` rejects writes to other tenants' rows.
4. **PostgreSQL RLS (`sql/rls.sql`, optional)** – the session sets `app.current_tenant` per transaction; policies enforce the same rule inside the database, even for raw SQL.

The tenant id comes only from the verified JWT (`tid` claim), never from headers/paths/bodies.
