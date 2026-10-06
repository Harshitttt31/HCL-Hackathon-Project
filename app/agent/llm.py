"""LLM access. The LLM is an optional, constrained helper: it may reword a deterministic answer or break a tie
between allowlisted intents. It never supplies a fact, a number, a citation or a decision.

Backends: ollama (local, default), cloud (optional OpenAI-compatible fallback), mock (deterministic, no model).
Every call returns None on failure so the caller can use its deterministic fallback.
"""
from __future__ import annotations

import json
import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class LLMClient(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        """Return a parsed JSON object, or None if the model is unavailable or produced invalid JSON."""

    @abstractmethod
    def healthy(self) -> bool: ...

    @property
    def is_mock(self) -> bool:
        return False


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class MockLLM(LLMClient):
    """Deterministic stand-in so the whole pipeline runs and is testable without a model.

    For the rewording task it returns the deterministic draft unchanged. For classification it abstains.
    """

    name = "mock"
    model = "mock"

    @property
    def is_mock(self) -> bool:
        return True

    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        m = re.search(r"<draft>(.*?)</draft>", user, flags=re.DOTALL)
        if m:
            return {"answer": m.group(1).strip()}
        return None

    def healthy(self) -> bool:
        return True


class OllamaLLM(LLMClient):
    name = "ollama"

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.ollama_base_url.rstrip("/")
        self.model = s.llm_model
        self.timeout = s.llm_timeout_seconds
        self.temperature = s.llm_temperature
        self.retries = max(0, s.llm_max_retries)
        self.think = s.llm_think
        self._down_until = 0.0

    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        if time.monotonic() < self._down_until:
            return None  # circuit breaker: do not make every request wait for a dead model
        payload = {"model": self.model, "stream": False, "format": "json",
                   "options": {"temperature": self.temperature},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.think is not None:
            payload["think"] = self.think
        for attempt in range(self.retries + 1):
            try:
                r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
                r.raise_for_status()
                obj = extract_json(r.json().get("message", {}).get("content", ""))
                if obj is not None:
                    return obj
                log.warning("llm_invalid_json", extra={"attempt": attempt})
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("llm_call_failed", extra={"attempt": attempt, "error": str(exc)[:200]})
                self._down_until = time.monotonic() + 15.0
                break
        return None

    def healthy(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


class CloudLLM(LLMClient):
    """Optional OpenAI-compatible fallback. Disabled unless base URL, key and model are configured."""

    name = "cloud"

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = (s.cloud_llm_base_url or "").rstrip("/")
        self.key = s.cloud_llm_api_key or ""
        self.model = s.cloud_llm_model or ""
        self.timeout = s.llm_timeout_seconds
        self.temperature = s.llm_temperature

    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        if not (self.base_url and self.key and self.model):
            return None
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout,
                           headers={"Authorization": f"Bearer {self.key}"},
                           json={"model": self.model, "temperature": self.temperature, "response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            return extract_json(r.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            log.warning("cloud_llm_failed", extra={"error": str(exc)[:200]})
            return None

    def healthy(self) -> bool:
        return bool(self.base_url and self.key and self.model)


class FallbackLLM(LLMClient):
    """Primary backend with an optional secondary; the first non-empty answer wins."""

    def __init__(self, primary: LLMClient, secondary: Optional[LLMClient]):
        self.primary, self.secondary = primary, secondary
        self.name = primary.name + (f"+{secondary.name}" if secondary else "")
        self.model = primary.model

    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        out = self.primary.complete_json(system, user)
        if out is None and self.secondary is not None:
            out = self.secondary.complete_json(system, user)
        return out

    def healthy(self) -> bool:
        return self.primary.healthy() or bool(self.secondary and self.secondary.healthy())

    @property
    def is_mock(self) -> bool:
        return self.primary.is_mock


class MeteredLLM(LLMClient):
    """Counts calls and characters so the evaluation can report LLM cost per question (tokens are estimated as chars / 4)."""

    def __init__(self, inner: LLMClient):
        self.inner = inner
        self.name, self.model = inner.name, inner.model
        self.calls = 0
        self.prompt_chars = 0
        self.completion_chars = 0

    def complete_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        self.calls += 1
        self.prompt_chars += len(system) + len(user)
        out = self.inner.complete_json(system, user)
        if out is not None:
            self.completion_chars += len(json.dumps(out))
        return out

    def healthy(self) -> bool:
        return self.inner.healthy()

    @property
    def is_mock(self) -> bool:
        return self.inner.is_mock

    def snapshot(self) -> dict[str, int]:
        return {"calls": self.calls, "prompt_tokens_est": self.prompt_chars // 4, "completion_tokens_est": self.completion_chars // 4}


_lock = threading.Lock()
_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    with _lock:
        if _client is None:
            s = get_settings()
            backend = s.effective_llm_backend
            if backend == "mock":
                _client = MockLLM()
            elif backend == "cloud":
                _client = CloudLLM()
            else:
                cloud = CloudLLM() if (s.cloud_llm_base_url and s.cloud_llm_api_key and s.cloud_llm_model) else None
                _client = FallbackLLM(OllamaLLM(), cloud) if cloud else OllamaLLM()
            _client = MeteredLLM(_client)
        return _client


def reset_llm() -> None:
    global _client
    with _lock:
        _client = None
