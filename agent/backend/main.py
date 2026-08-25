"""AI Operations Control — backend API.

Supersedes `web.py`: this process serves **only** the JSON API (no embedded
HTML/JS — the SPA is a separate `frontend/` project, see docker-compose.yml).
Everything that used to live in `app.state` in-memory now persists in
PostgreSQL (SQLite in tests), behind JWT auth + role-based access control.

Run:  uvicorn backend.main:app --host 0.0.0.0 --port 8002
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select

from core.mcp_manager import DynamicMCPManager

from backend import state
from backend.config import get_settings
from backend.crypto import encrypt_secret
from backend.db import SessionLocal, init_models
from backend.models import LlmConfig, McpServerConfig, ProcessFlow, User
from backend.routers import agent_runs, audit, auth, chat, flows, llms, mcp_servers, schedules, system, users
from backend.security import hash_password
from backend.seed_data import BUILTIN_FLOWS
from backend.services import scheduler as sched_svc

settings = get_settings()


async def _seed_if_empty() -> None:
    """Idempotent first-boot seed: admin user, LLM registry from env keys,
    built-in Deep Dive flows. No-op on every boot after the first."""
    async with SessionLocal() as db:
        if (await db.execute(select(User).limit(1))).first() is None:
            db.add(User(email=settings.admin_email.strip().lower(),
                        hashed_password=hash_password(settings.admin_password), role="admin"))

        if (await db.execute(select(LlmConfig).limit(1))).first() is None:
            active = settings.llm_provider.lower()

            def add(slug, name, kind, default_model="", base_url="", api_key=""):
                model = settings.llm_model if (slug == active and settings.llm_model) else default_model
                db.add(LlmConfig(slug=slug, name=name, kind=kind, model=model, base_url=base_url,
                                  encrypted_api_key=encrypt_secret(api_key) if api_key else None,
                                  is_default=(slug == active)))

            add("mock", "Mock (no LLM)", "mock")
            if settings.openrouter_api_key:
                add("openrouter", "OpenRouter", "openai-compatible", "qwen/qwen3-30b-a3b-instruct-2507",
                    "https://openrouter.ai/api/v1", settings.openrouter_api_key)
            if settings.anthropic_api_key:
                add("claude", "Claude (Anthropic)", "anthropic", "claude-opus-4-8", "", settings.anthropic_api_key)
            if settings.openai_api_key:
                add("openai", "OpenAI", "openai", "gpt-4o", "", settings.openai_api_key)
            if settings.mistral_api_key:
                add("mistral", "Mistral", "mistral", "mistral-large-latest", "", settings.mistral_api_key)
            # If LLM_PROVIDER didn't match anything seeded (e.g. no key for it), default to mock.
            await db.flush()
            if (await db.execute(select(LlmConfig).where(LlmConfig.is_default.is_(True)))).first() is None:
                mock = (await db.execute(select(LlmConfig).where(LlmConfig.slug == "mock"))).scalar_one()
                mock.is_default = True

        if (await db.execute(select(ProcessFlow).limit(1))).first() is None:
            for data in BUILTIN_FLOWS:
                db.add(ProcessFlow(**data, is_builtin=True))

        await db.commit()


async def _connect_mcp_servers() -> DynamicMCPManager:
    manager = DynamicMCPManager()
    seen: set[str] = set()
    async with SessionLocal() as db:
        for url in [u.strip() for u in settings.mcp_server_urls.split(",") if u.strip()]:
            slug = None
            try:
                view = await manager.add(url)
                slug = view["id"]
            except Exception as exc:  # noqa: BLE001 — keep booting even if one is down
                print(f"[backend] could not connect MCP server {url}: {exc}")
                continue
            seen.add(slug)
            existing = await db.execute(select(McpServerConfig).where(McpServerConfig.slug == slug))
            if existing.scalar_one_or_none() is None:
                db.add(McpServerConfig(slug=slug, url=url))
        # then reconnect anything persisted from a previous run (e.g. added via the UI)
        persisted = (await db.execute(select(McpServerConfig))).scalars().all()
        for row in persisted:
            if row.slug in seen:
                continue
            try:
                await manager.add(row.url, row.slug)
            except Exception as exc:  # noqa: BLE001
                print(f"[backend] could not reconnect persisted MCP server {row.slug} ({row.url}): {exc}")
        await db.commit()
    return manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url.startswith("sqlite"):
        await init_models()   # dev/test convenience; Postgres uses Alembic (see Dockerfile entrypoint)
    await _seed_if_empty()

    manager = await _connect_mcp_servers()
    app.state.mcp = manager
    state.mcp_manager = manager

    sched_svc.configure_scheduler()
    sched_svc.scheduler.start()

    try:
        yield
    finally:
        sched_svc.scheduler.shutdown(wait=False)
        await manager.aclose()


app = FastAPI(title="AI Operations Control — API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,   # required for the httpOnly refresh cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = auth.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak stack traces/internal details to the client; full details still
    # go to stdout/stderr for the deployment's own log collection.
    print(f"[backend] unhandled error on {request.method} {request.url.path}: {exc!r}")
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


for router in (auth.router, users.router, system.router, chat.router, agent_runs.router,
               schedules.router, mcp_servers.router, llms.router, flows.router, audit.router):
    app.include_router(router)
