"""FastAPI application entry point.  Run:  uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

import app.tenancy  # noqa: F401  (registers the tenant-isolation session event listeners)
from app.config import get_settings
from app.database import engine, init_db
from app.exceptions import AppError
from app.routers import auth, subscriptions, tenants, users

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_create_tables:
        init_db()
    yield
    engine.dispose()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
        headers=headers,
    )


for router in (auth.router, tenants.router, users.router, subscriptions.router):
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
