"""
llm_runtime_config.py - the ONE place that decides which LLM config is
actually in effect for nl2q/codegen/query_advisor, so none of them have to
know whether it came from an admin's saved setting or from env vars.

Why this exists: app/config.py's `settings` object is built once at process
startup from NL2Q_LLM_* env vars - fine as a bootstrap default, but it means
changing provider/model/key would otherwise require editing .env and
restarting the container. The Model Settings page (routers/llm_config.py,
web-ui/src/pages/ModelSettings.jsx) writes to the `llmconfig` table instead,
and this module prefers that row - read fresh on every call, so a saved
change takes effect on the very next request, no restart.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from .config import settings
from .db import engine
from .models import LLMConfig


@dataclass(frozen=True)
class EffectiveLLMConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_sec: float
    source: str  # "admin" | "env" - surfaced in the settings UI, not load-bearing


def get() -> EffectiveLLMConfig:
    with Session(engine) as session:
        row = session.exec(select(LLMConfig).where(LLMConfig.id == 1)).first()
    if row is not None:
        return EffectiveLLMConfig(
            provider=row.provider, model=row.model, api_key=row.api_key,
            base_url=row.base_url, timeout_sec=row.timeout_sec, source="admin",
        )
    return EffectiveLLMConfig(
        provider=settings.nl2q_llm_provider, model=settings.nl2q_llm_model,
        api_key=settings.nl2q_llm_api_key, base_url=settings.nl2q_llm_base_url,
        timeout_sec=settings.nl2q_llm_timeout_sec, source="env",
    )


def save(*, provider: str, model: str, api_key: str | None, base_url: str,
         timeout_sec: float, updated_by: str) -> EffectiveLLMConfig:
    """api_key=None (or "") means "leave the stored key as-is" for a re-save
    of the SAME provider - the settings UI never round-trips the real key
    back to the browser (see routers/llm_config.py), so a blank submission
    must not clobber it there. But a key is provider-specific: switching
    provider without supplying a new key clears the old one instead of
    silently carrying, say, a Claude key over to a freshly-selected OpenAI
    config - keeping it would be a stale-credential footgun, not a
    convenience, the moment the new provider actually requires auth."""
    with Session(engine) as session:
        row = session.exec(select(LLMConfig).where(LLMConfig.id == 1)).first()
        if row is None:
            row = LLMConfig(id=1)
            session.add(row)
        provider_changed = row.provider != provider
        row.provider = provider
        row.model = model
        if api_key:
            row.api_key = api_key
        elif provider_changed:
            row.api_key = ""
        row.base_url = base_url
        row.timeout_sec = timeout_sec
        row.updated_by = updated_by
        from datetime import datetime, timezone
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return EffectiveLLMConfig(
            provider=row.provider, model=row.model, api_key=row.api_key,
            base_url=row.base_url, timeout_sec=row.timeout_sec, source="admin",
        )
