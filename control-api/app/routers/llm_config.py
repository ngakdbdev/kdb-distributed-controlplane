"""
llm_config.py (router) - platform-admin settings for the nl2q/codegen LLM
provider (app/llm_runtime_config.py). Lets an admin switch between the
self-hosted on-prem model (Ollama) and an external network-calling one
(Claude, or any other OpenAI-compatible host) from the UI, taking effect on
the next request - no editing .env, no container restart.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import llm_runtime_config
from .auth import CurrentUser, require_platform_admin

router = APIRouter(prefix="/admin/llm-config", tags=["admin"])


class LLMConfigOut(BaseModel):
    provider: str
    model: str
    base_url: str
    timeout_sec: float
    api_key_set: bool          # never the key itself
    source: str                # "admin" | "env"


class LLMConfigIn(BaseModel):
    provider: str
    model: str = ""
    api_key: str | None = None  # None/"" = leave the stored key unchanged
    base_url: str = ""
    timeout_sec: float = 20.0


def _out(cfg: llm_runtime_config.EffectiveLLMConfig) -> LLMConfigOut:
    return LLMConfigOut(provider=cfg.provider, model=cfg.model, base_url=cfg.base_url,
                        timeout_sec=cfg.timeout_sec, api_key_set=bool(cfg.api_key), source=cfg.source)


@router.get("", response_model=LLMConfigOut)
def get_config(user: CurrentUser = Depends(require_platform_admin)):
    return _out(llm_runtime_config.get())


@router.put("", response_model=LLMConfigOut)
def update_config(body: LLMConfigIn, user: CurrentUser = Depends(require_platform_admin)):
    cfg = llm_runtime_config.save(
        provider=body.provider, model=body.model, api_key=body.api_key,
        base_url=body.base_url, timeout_sec=body.timeout_sec, updated_by=user.email,
    )
    return _out(cfg)
