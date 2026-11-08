"""End-to-end tests against a real PostgreSQL 18 database.

    export DATABASE_URL=postgresql+psycopg://billing:billing@localhost:5432/billing_test
    export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
    export AUTO_CREATE_TABLES=true
    pytest -q
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _signup(client, slug: str | None = None):
    slug = slug or f"acme-{uuid.uuid4().hex[:10]}"
    email = f"owner@{slug}.com"
    r = client.post(
        "/api/v1/tenants",
        json={
            "tenant_name": "Acme Inc", "slug": slug, "owner_email": email,
            "owner_password": PASSWORD, "owner_full_name": "Olivia Owner",
        },
    )
    assert r.status_code == 201, r.text
    return slug, email, r.json()


def _login(client, slug, email, password=PASSWORD):
    r = client.post("/api/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    return r


def _auth(client, slug, email):
    r = _login(client, slug, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_signup_creates_owner_and_free_subscription(client):
    slug, email, body = _signup(client)
    assert body["owner"]["role"] == "owner"
    assert body["subscription"]["plan_name"] == "free"
    assert body["subscription"]["status"] == "active"


def test_duplicate_slug_conflicts_and_rolls_back(client):
    slug, _, _ = _signup(client)
    r = client.post(
        "/api/v1/tenants",
        json={"tenant_name": "Other", "slug": slug, "owner_email": "x@example.com",
              "owner_password": PASSWORD, "owner_full_name": "X"},
    )
    assert r.status_code == 409


def test_login_failures_are_generic(client):
    slug, email, _ = _signup(client)
    assert _login(client, slug, email, "wrong-password-123").status_code == 401
    assert _login(client, slug, "nobody@example.com").status_code == 401
    assert _login(client, "no-such-tenant", email).status_code == 401


def test_cross_tenant_isolation(client):
    slug_a, email_a, _ = _signup(client)
    slug_b, email_b, _ = _signup(client)
    ha, hb = _auth(client, slug_a, email_a), _auth(client, slug_b, email_b)

    # same email may exist in two tenants
    r = client.post("/api/v1/users", headers=ha,
                    json={"email": "shared@example.com", "password": PASSWORD, "full_name": "Sam"})
    assert r.status_code == 201
    user_a = r.json()
    r = client.post("/api/v1/users", headers=hb,
                    json={"email": "shared@example.com", "password": PASSWORD, "full_name": "Sam"})
    assert r.status_code == 201

    # tenant B cannot read / modify / delete tenant A's user, nor see it in lists
    assert client.get(f"/api/v1/users/{user_a['id']}", headers=hb).status_code == 404
    assert client.patch(f"/api/v1/users/{user_a['id']}", headers=hb, json={"full_name": "Hacked"}).status_code == 404
    assert client.delete(f"/api/v1/users/{user_a['id']}", headers=hb).status_code == 404
    ids_b = {u["id"] for u in client.get("/api/v1/users", headers=hb).json()["items"]}
    assert user_a["id"] not in ids_b
    assert client.get("/api/v1/users", headers=hb).json()["total"] == 2

    # subscriptions are isolated too
    sub_a = client.get("/api/v1/subscriptions/current", headers=ha).json()
    sub_b = client.get("/api/v1/subscriptions/current", headers=hb).json()
    assert sub_a["id"] != sub_b["id"] and sub_a["tenant_id"] != sub_b["tenant_id"]


def test_role_permissions_and_last_owner_rule(client):
    slug, email, _ = _signup(client)
    h = _auth(client, slug, email)
    r = client.post("/api/v1/users", headers=h,
                    json={"email": "adm@example.com", "password": PASSWORD, "full_name": "Ada", "role": "admin"})
    assert r.status_code == 201
    r = client.post("/api/v1/users", headers=h,
                    json={"email": "mem@example.com", "password": PASSWORD, "full_name": "Max"})
    member = r.json()
    h_admin = _auth(client, slug, "adm@example.com")
    h_member = _auth(client, slug, "mem@example.com")

    assert client.get("/api/v1/users", headers=h_member).status_code == 403
    # admin cannot create admins or touch subscriptions
    assert client.post("/api/v1/users", headers=h_admin,
                       json={"email": "a2@example.com", "password": PASSWORD, "full_name": "A2", "role": "admin"}).status_code == 403
    assert client.post("/api/v1/subscriptions/current/cancel", headers=h_admin, json={}).status_code == 403
    # admin can manage members
    assert client.patch(f"/api/v1/users/{member['id']}", headers=h_admin, json={"full_name": "Maxine"}).status_code == 200

    # the only owner cannot be demoted/deleted (promote a 2nd owner, then it works)
    me = client.get("/api/v1/users/me", headers=h).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=h, json={"role": "admin"}).status_code == 422


def test_subscription_lifecycle(client):
    slug, email, _ = _signup(client)
    h = _auth(client, slug, email)

    assert client.post("/api/v1/subscriptions", headers=h, json={"plan_name": "pro"}).status_code == 409  # one live sub only

    r = client.post("/api/v1/subscriptions/current/change-plan", headers=h, json={"plan_name": "pro"})
    assert r.status_code == 200 and r.json()["plan_name"] == "pro"
    assert client.post("/api/v1/subscriptions/current/change-plan", headers=h, json={"plan_name": "pro"}).status_code == 422

    r = client.post("/api/v1/subscriptions/current/cancel", headers=h, json={"at_period_end": True})
    assert r.json()["cancel_at_period_end"] is True and r.json()["status"] == "active"
    r = client.post("/api/v1/subscriptions/current/cancel", headers=h, json={"at_period_end": False})
    assert r.json()["status"] == "canceled"
    assert client.get("/api/v1/subscriptions/current", headers=h).status_code == 404

    r = client.post("/api/v1/subscriptions", headers=h, json={"plan_name": "enterprise", "trial_days": 14})
    assert r.status_code == 201 and r.json()["status"] == "trialing"
    assert client.get("/api/v1/subscriptions", headers=h).json()["total"] == 2


def test_tenant_deactivation_blocks_access(client):
    slug, email, _ = _signup(client)
    h = _auth(client, slug, email)
    assert client.delete("/api/v1/tenants/me", headers=h).status_code == 204
    assert client.get("/api/v1/users/me", headers=h).status_code == 401
    assert _login(client, slug, email).status_code == 401


def test_requires_valid_token(client):
    assert client.get("/api/v1/users/me").status_code == 401
    assert client.get("/api/v1/users/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
