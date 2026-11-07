"""LLM client seam. Gemini (Google AI Studio key) in production; fake/replay for tests and offline demo.

Every call is audited: call name, model, prompt hash, schema hash, output, latency.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .audit import AuditLog, sha
from .config import Config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    def generate(self, call: str, system: str, user: str, schema: type[T], model: str, key: str = "") -> T: ...


def _schema_hash(schema: type[BaseModel]) -> str:
    return sha(json.dumps(schema.model_json_schema(), sort_keys=True))


class _Audited:
    audit: AuditLog | None = None
    name = "base"

    def _log(self, call, model, system, user, schema, output, t0, error=None, key=""):
        if self.audit is None:
            return
        self.audit.append("llm_call", {
            "client": self.name, "call": call, "model": model, "key": key,
            "prompt_hash": sha(system + "\n\n" + user), "schema_hash": _schema_hash(schema),
            "output": output, "error": error, "latency_s": round(time.time() - t0, 3),
        })


class GeminiClient(_Audited):
    """Google Gen AI SDK (`google-genai`) with an AI Studio API key.

    Structured output via response_json_schema; we still validate locally with pydantic.
    """
    name = "gemini"

    def __init__(self, cfg: Config, audit: AuditLog | None = None):
        if not cfg.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set. Put your Google AI Studio key in .env as GEMINI_API_KEY=...")
        from google import genai  # imported lazily so tests don't need the SDK

        self._genai = genai
        self.client = genai.Client(api_key=cfg.gemini_api_key)
        self.cfg = cfg
        self.audit = audit

    def generate(self, call, system, user, schema, model, key=""):
        from google.genai import types

        t0 = time.time()
        conf = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.cfg.temperature,
            max_output_tokens=self.cfg.max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
        )
        last_err = None
        for attempt in range(2):  # one retry on malformed JSON / transient error
            try:
                resp = self.client.models.generate_content(model=model, contents=user, config=conf)
                text = resp.text or ""
                out = schema.model_validate_json(text)
                self._log(call, model, system, user, schema, out.model_dump(), t0, key=key)
                return out
            except (ValidationError, ValueError) as e:
                last_err = f"invalid JSON from model: {e}"
            except Exception as e:  # network / quota / safety block
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.5 * (attempt + 1))
        self._log(call, model, system, user, schema, None, t0, error=last_err, key=key)
        raise LLMError(last_err)


class FakeLLM(_Audited):
    """Test double: responder(call, user, schema, key) -> dict | BaseModel."""
    name = "fake"

    def __init__(self, responder: Callable[..., dict | BaseModel], audit: AuditLog | None = None):
        self.responder = responder
        self.audit = audit
        self.calls: list[dict] = []

    def generate(self, call, system, user, schema, model, key=""):
        t0 = time.time()
        self.calls.append({"call": call, "user": user, "key": key, "system": system})
        raw = self.responder(call=call, user=user, schema=schema, key=key)
        if raw is None:
            raise LLMError("fake LLM has no response")
        out = raw if isinstance(raw, BaseModel) else schema.model_validate(raw)
        self._log(call, model, system, user, schema, out.model_dump(), t0, key=key)
        return out


class ReplayLLM(FakeLLM):
    """Offline demo: serves recorded outputs from a JSON file keyed by "<call>:<key>"."""
    name = "replay"

    def __init__(self, path: Path, audit: AuditLog | None = None):
        data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
        super().__init__(lambda call, key, **_: data.get(f"{call}:{key}"), audit)


def make_llm(cfg: Config, audit: AuditLog | None, replay: bool) -> LLMClient:
    if replay:
        return ReplayLLM(cfg.replay_file, audit)
    return GeminiClient(cfg, audit)  # raises a clear error if GEMINI_API_KEY is missing


class RecordingLLM:
    """Wraps a live client and keeps every output keyed like ReplayLLM, so a live run can be replayed offline."""

    def __init__(self, inner: LLMClient):
        self.inner, self.name, self.recorded = inner, inner.name, {}

    def generate(self, call, system, user, schema, model, key=""):
        out = self.inner.generate(call, system, user, schema, model, key)
        self.recorded[f"{call}:{key}"] = out.model_dump()
        return out
