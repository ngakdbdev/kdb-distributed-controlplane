"""
llm_provider.py - a tiny, swappable text-completion client used by the
natural-language-to-q feature (routers/query.py: POST /query/nl2q).

Two provider families cover practically every deployment shape this
control plane runs in (see fleet_agent/backends.py: aws/azure/gcp/on-prem)
without pulling in a heavyweight abstraction layer:

  * "anthropic"          the official Anthropic SDK against the Claude API
                          (or a private Anthropic-compatible gateway, via
                          NL2Q_LLM_BASE_URL).
  * "openai_compatible"  a plain HTTP POST against an OpenAI-shaped
                          /chat/completions endpoint. This covers OpenAI
                          itself and Azure OpenAI, and - the important case
                          for an on-prem/air-gapped TickHouse that cannot
                          call out to a public API - a local model server
                          (Ollama, vLLM, LM Studio) via NL2Q_LLM_BASE_URL.

Provider/model/key/base_url are NOT read from env vars directly here - see
llm_runtime_config.py, which prefers a platform admin's saved Model Settings
(routers/llm_config.py) over the NL2Q_LLM_* env vars, re-read on every call
so a saved change takes effect immediately, no container restart needed.
provider=none (the default until an admin configures one, or the env var
fallback) disables this module entirely: the frontend's offline regex
generator keeps working with zero backend calls, so the feature degrades
gracefully rather than requiring a key to exist at all.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import llm_runtime_config


class LLMError(RuntimeError):
    """Any provider-call failure; callers decide how to degrade."""


def configured() -> bool:
    return llm_runtime_config.get().provider in ("anthropic", "openai_compatible")


def complete(system: str, user: str, max_tokens: int = 1024) -> str:
    """One-shot system+user completion, no conversation state or tools -
    this exists purely to translate one NL request into one q expression.
    max_tokens defaults generously but callers should pass a tight bound for
    their own output shape (nl2q's is one line; a runaway generation on a
    slow local model is a real, measured latency cost, not just a token-
    count nicety)."""
    cfg = llm_runtime_config.get()
    if cfg.provider == "anthropic":
        return _complete_anthropic(cfg, system, user, max_tokens)
    if cfg.provider == "openai_compatible":
        return _complete_openai_compatible(cfg, system, user, max_tokens)
    raise LLMError(f"no LLM provider configured (provider={cfg.provider!r})")


def _complete_anthropic(cfg: llm_runtime_config.EffectiveLLMConfig, system: str, user: str,
                        max_tokens: int) -> str:
    try:
        import anthropic
    except ImportError as exc:  # packaging error, not a request-time condition
        raise LLMError("the 'anthropic' package is not installed") from exc

    kwargs = {}
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    client = anthropic.Anthropic(timeout=cfg.timeout_sec, **kwargs)
    try:
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"anthropic call failed: {exc}") from exc
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise LLMError("anthropic call returned no text")
    return text


def _complete_openai_compatible(cfg: llm_runtime_config.EffectiveLLMConfig, system: str, user: str,
                                max_tokens: int) -> str:
    base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    body = json.dumps({
        "model": cfg.model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise LLMError(f"openai-compatible call failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"openai-compatible call failed: {exc}") from exc
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"openai-compatible call returned unexpected payload: {payload}") from exc
    if not text or not text.strip():
        raise LLMError("openai-compatible call returned no text")
    return text
