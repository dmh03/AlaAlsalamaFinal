"""Single wrapper around the Anthropic SDK. Records every call in llm_calls.

Optional disk cache (eval only): set llm.CACHE_DIR to a Path. Cached hits are free and identical
on re-run; they still write an llm_calls row (with the original token counts, latency 0) so cost
per patient can be computed from the table.
"""
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Type, TypeVar

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

from .db import ROOT, session
from .models import LLMCall

load_dotenv(ROOT / ".env")

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
T = TypeVar("T", bound=BaseModel)

_client = None
_current = None
CACHE_DIR: Optional[Path] = None
LOGGING = True
SKIP: set = set()
_tl = threading.local()


@contextmanager
def no_logging():
    """Calls made inside are not recorded in llm_calls (used for the patient simulator: not system cost)."""
    _tl.off = True
    try:
        yield
    finally:
        _tl.off = False


class LLMUnavailable(Exception):
    pass


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("ALASALAMA_NO_LLM") != "1"


def bind(s) -> None:
    global _current
    _current = s


def prompt(name: str) -> str:
    return (ROOT / "alasalama" / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def wrap_untrusted(text: str) -> str:
    return f"<data>\n{text}\n</data>"


def _client_get():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(timeout=60.0, max_retries=3)
    return _client


def _log(now, episode_id, purpose, model, input_tokens, output_tokens, ms, ok):
    if not LOGGING or getattr(_tl, "off", False):
        return
    row = LLMCall(at=now, episode_id=episode_id, purpose=purpose, model=model,
                  input_tokens=input_tokens or 0, output_tokens=output_tokens or 0, latency_ms=ms, ok=ok)
    if _current is not None:
        _current.add(row)
        return
    with session() as s:
        s.add(row)
        s.commit()


def _trace(purpose, model, system, user, output, episode_id, ok=True):
    """Opt-in (ALASALAMA_TRACE=1): append the exact prompt and output to data/logs/llm_trace.jsonl."""
    if os.environ.get("ALASALAMA_TRACE") != "1" or getattr(_tl, "off", False):
        return
    try:
        p = ROOT / "data" / "logs" / "llm_trace.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "episode_id": episode_id, "purpose": purpose,
               "model": model, "ok": ok, "system": system, "input": user, "output": output}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - tracing must never break the agent
        pass


def _key(kind, model, system, user, schema_name="") -> Optional[Path]:
    if CACHE_DIR is None:
        return None
    h = hashlib.sha256(json.dumps([kind, model, system, user, schema_name], ensure_ascii=False).encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{h}.json"


def _cache_get(p: Optional[Path]):
    return json.loads(p.read_text(encoding="utf-8")) if p is not None and p.exists() else None


def _cache_put(p: Optional[Path], payload: dict):
    if p is not None:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def complete(purpose: str, model: str, system: str, user: str, now, episode_id: Optional[int] = None,
             max_tokens: int = 600) -> str:
    if not available() or purpose in SKIP:
        raise LLMUnavailable("no API key or purpose skipped")
    ck = _key("complete", model, system, user)
    hit = _cache_get(ck)
    if hit:
        _log(now, episode_id, purpose, model, hit["in"], hit["out"], 0, True)
        _trace(purpose, model, system, user, hit["text"], episode_id)
        return hit["text"]
    t0 = time.time()
    try:
        r = _client_get().messages.create(model=model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001
        _log(now, episode_id, purpose, model, 0, 0, int((time.time() - t0) * 1000), False)
        raise LLMUnavailable(str(e)) from e
    _log(now, episode_id, purpose, model, r.usage.input_tokens, r.usage.output_tokens, int((time.time() - t0) * 1000), True)
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    _cache_put(ck, {"text": text, "in": r.usage.input_tokens, "out": r.usage.output_tokens})
    _trace(purpose, model, system, user, text, episode_id)
    return text


def parse(purpose: str, model: str, system: str, user: str, schema: Type[T], now,
          episode_id: Optional[int] = None, max_tokens: int = 1000) -> T:
    """Structured output via client.messages.parse (output_format=PydanticModel -> parsed_output)."""
    if not available() or purpose in SKIP:
        raise LLMUnavailable("no API key or purpose skipped")
    ck = _key("parse", model, system, user, schema.__name__)
    hit = _cache_get(ck)
    if hit:
        _log(now, episode_id, purpose, model, hit["in"], hit["out"], 0, True)
        _trace(purpose, model, system, user, hit["json"], episode_id)
        return schema.model_validate_json(hit["json"])
    t0 = time.time()
    try:
        r = _client_get().messages.parse(model=model, max_tokens=max_tokens, system=system,
                                         messages=[{"role": "user", "content": user}], output_format=schema)
    except Exception as e:  # noqa: BLE001
        _log(now, episode_id, purpose, model, 0, 0, int((time.time() - t0) * 1000), False)
        raise LLMUnavailable(str(e)) from e
    _log(now, episode_id, purpose, model, r.usage.input_tokens, r.usage.output_tokens, int((time.time() - t0) * 1000), True)
    if r.parsed_output is None:
        _trace(purpose, model, system, user, "", episode_id, ok=False)
        raise LLMUnavailable("unparseable output")
    _trace(purpose, model, system, user, r.parsed_output.model_dump_json(), episode_id)
    _cache_put(ck, {"json": r.parsed_output.model_dump_json(), "in": r.usage.input_tokens, "out": r.usage.output_tokens})
    return r.parsed_output


def cost_usd(call: LLMCall) -> float:
    prices = yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text())
    p = prices.get(call.model, {"input": 0, "output": 0})
    return (call.input_tokens * p["input"] + call.output_tokens * p["output"]) / 1_000_000
