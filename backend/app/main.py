"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.pool import close_pool, create_pool
from app.routes.health import router as health_router
from app.routes.profiles import router as profiles_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the DB pool once at startup (lazily — see db/pool.py); close it on shutdown."""
    pool = await create_pool(settings.database_url)
    app.state.pool = pool
    try:
        yield
    finally:
        await close_pool(pool)


app = FastAPI(title="Coach Bill API", lifespan=lifespan)

# NOTE: when auth lands and we need allow_credentials=True, cors_origins must stay an
# explicit allowlist — never "*" (credentials + wildcard origin is the classic CORS hole).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(profiles_router)


@app.get("/")
def root() -> dict[str, str]:
    """Liveness check: proves the API process is up (no dependencies touched)."""
    return {"service": "coach-bill-backend", "status": "ok"}
