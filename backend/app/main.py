"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.pool import close_pool, create_pool
from app.routes.health import router as health_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the DB connection pool once at startup; close it on shutdown."""
    pool = await create_pool(settings.database_url)
    app.state.pool = pool
    try:
        yield
    finally:
        await close_pool(pool)


app = FastAPI(title="Coach Bill API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)


@app.get("/")
def root() -> dict[str, str]:
    """Liveness check: proves the API process is up (no dependencies touched)."""
    return {"service": "coach-bill-backend", "status": "ok"}
