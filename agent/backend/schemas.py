"""Pydantic request/response schemas.

Request bodies are a direct continuation of the ones `web.py` used
(`ChatIn`, `HeadlessIn` → `RunIn`, `TriggerIn` → `ScheduleIn`, `ToolIn`,
`McpIn`, `McpUpdateIn`, `LlmIn`, `LlmUpdateIn`, `FlowStep`, `FlowIn`) plus the
new auth/user/schedule-output shapes.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from backend.models import ROLES


# --------------------------------------------------------------------------- #
# Auth / users                                                                 #
# --------------------------------------------------------------------------- #
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = "viewer"


class UserUpdateIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


# --------------------------------------------------------------------------- #
# Chat / one-shot runs                                                         #
# --------------------------------------------------------------------------- #
class ChatIn(BaseModel):
    session_id: str
    message: str
    provider: str | None = None    # an LLM registry id
    model: str | None = None


class ChatOut(BaseModel):
    answer: str
    tool_calls: list[dict] = []
    error: bool = False
    provider: str
    model: str


class RunIn(BaseModel):
    question: str
    provider: str | None = None
    model: str | None = None


class RunOut(BaseModel):
    id: int
    source: str
    llm_label: str
    model: str
    question: str
    answer: str
    tool_calls: list[dict]
    error: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolIn(BaseModel):
    name: str
    arguments: dict = {}


# --------------------------------------------------------------------------- #
# Schedules (replaces TriggerIn / the asyncio-loop triggers)                   #
# --------------------------------------------------------------------------- #
class ScheduleIn(BaseModel):
    label: str | None = None
    question: str
    provider: str | None = None    # LLM id (int as str) or None = default
    model: str | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = None


class ScheduleOut(BaseModel):
    id: int
    label: str
    question: str
    llm_id: int | None
    model: str
    cron_expression: str | None
    interval_seconds: int | None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# MCP servers                                                                  #
# --------------------------------------------------------------------------- #
class McpIn(BaseModel):
    url: str
    id: str | None = None


class McpUpdateIn(BaseModel):
    url: str


# --------------------------------------------------------------------------- #
# LLM configs                                                                  #
# --------------------------------------------------------------------------- #
class LlmIn(BaseModel):
    name: str
    kind: str
    model: str | None = ""
    base_url: str | None = ""
    api_key: str | None = ""


class LlmUpdateIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None     # empty string = leave the stored key unchanged


class LlmOut(BaseModel):
    id: str
    name: str
    kind: str
    model: str
    base_url: str
    has_key: bool
    is_default: bool
    needs_key: bool


# --------------------------------------------------------------------------- #
# Process flows                                                                #
# --------------------------------------------------------------------------- #
class FlowStep(BaseModel):
    system: str = ""
    tool: str
    what: str = ""
    why: str = ""
    args: dict = {}
    capture: list[str] = []
    skip_note: str | None = None


class FlowIn(BaseModel):
    name: str
    description: str = ""
    inputs: list[dict] = []
    steps: list[FlowStep] = []


class FlowOut(BaseModel):
    id: str
    name: str
    description: str
    inputs: list[dict]
    steps: list[dict]
    is_builtin: bool


# --------------------------------------------------------------------------- #
# Audit                                                                        #
# --------------------------------------------------------------------------- #
class AuditOut(BaseModel):
    id: int
    user_email: str | None
    action: str
    target_type: str
    target_id: str
    detail: dict
    created_at: datetime


ROLE_LITERAL = ROLES  # re-exported for routers that need to validate a role string
