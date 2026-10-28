"""The ONE place every API-based LLM call goes through. Model and effort come from config."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# $ per million tokens: (input, output, cache_read, cache_write)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
}
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class BudgetExceeded(RuntimeError):
    pass


class Usage:
    """Process-wide spend tracker, shared by all stages in one run."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.input = self.output = self.cache_read = self.cache_write = 0
        self.usd = 0.0
        self.budget_usd: float | None = None

    def add(self, model: str, u) -> None:
        p = PRICES.get(model, PRICES["claude-opus-5-5"])
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        with self._lock:
            self.calls += 1
            self.input += inp
            self.output += out
            self.cache_read += cr
            self.cache_write += cw
            self.usd += (inp * p[0] + out * p[1] + cr * p[2] + cw * p[3]) / 1e6

    def add_usd(self, usd: float) -> None:
        with self._lock:
            self.usd += usd

    def check(self) -> None:
        if self.budget_usd is not None and self.usd >= self.budget_usd:
            raise BudgetExceeded(f"daily budget ${self.budget_usd:.2f} reached (spent ${self.usd:.2f})")

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input, "output_tokens": self.output,
                "cache_read_tokens": self.cache_read, "cache_write_tokens": self.cache_write,
                "usd": round(self.usd, 4)}


USAGE = Usage()
_clients: dict[int, anthropic.AsyncAnthropic] = {}


def client() -> anthropic.AsyncAnthropic:
    """One async client per event loop (stages call asyncio.run separately)."""
    loop = asyncio.get_running_loop()
    if id(loop) not in _clients:
        _clients.clear()
        # Org-level keys (not scoped to a workspace) must name the workspace on every request.
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        _clients[id(loop)] = anthropic.AsyncAnthropic(
            max_retries=4, default_headers={"anthropic-workspace-id": ws} if ws else None)
    return _clients[id(loop)]


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema → structured-output schema (all fields required, no extra keys)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def fix(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return fix(dict(defs[node["$ref"].split("/")[-1]]))
            node = {k: fix(v) for k, v in node.items() if k not in ("title", "default", "minimum", "maximum")}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            return node
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    return fix(schema)


async def _create(model: str, system: str, user: str, effort: str, max_tokens: int,
                  output_format: dict | None):
    USAGE.check()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        # The system prompt is identical across calls of one kind, so cache it.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, **({"format": output_format} if output_format else {})},
    )
    if model in FALLBACK_MODELS:
        kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        kwargs["extra_body"] = {"fallbacks": "default"}
    resp = await client().messages.create(**kwargs)
    USAGE.add(model, resp.usage)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("hit max_tokens before finishing")
    return resp


# ---------------------------------------------------------------- DeepSeek (OpenAI-compatible API)
# Estimated $/M tokens (input, output, cache-hit input); check https://api-docs.deepseek.com/quick_start/pricing
DEEPSEEK_PRICES = {"deepseek-chat": (0.27, 1.10, 0.07), "deepseek-reasoner": (0.55, 2.19, 0.14)}
_ds_http = None


async def _deepseek_structured(model: str, system: str, user: str, schema: type[T], max_tokens: int) -> T:
    import httpx
    global _ds_http
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set in .env")
    if _ds_http is None or _ds_http.is_closed:
        _ds_http = httpx.AsyncClient(timeout=120)
    USAGE.check()
    shape = json.dumps(strict_schema(schema))
    body = {
        "model": model,
        "messages": [
            # Schema goes in the (stable, cacheable) system prompt; JSON mode needs the word "json" in the prompt.
            {"role": "system", "content": f"{system}\n\nRespond with only a JSON object matching this JSON schema:\n{shape}"},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": min(max_tokens, 8000),
        "temperature": 0,
    }
    r = None
    for attempt in range(4):
        r = await _ds_http.post("https://api.deepseek.com/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {key}"})
        if r.status_code not in (429, 500, 502, 503):
            break
        await asyncio.sleep(2 ** attempt)
    r.raise_for_status()
    d = r.json()
    u = d.get("usage") or {}
    p = DEEPSEEK_PRICES.get(model, DEEPSEEK_PRICES["deepseek-chat"])
    hit = u.get("prompt_cache_hit_tokens", 0) or 0
    miss = (u.get("prompt_tokens", 0) or 0) - hit
    with USAGE._lock:
        USAGE.calls += 1
        USAGE.input += miss
        USAGE.cache_read += hit
        USAGE.output += u.get("completion_tokens", 0) or 0
        USAGE.usd += (miss * p[0] + hit * p[2] + (u.get("completion_tokens", 0) or 0) * p[1]) / 1e6
    text = d["choices"][0]["message"]["content"] or ""
    return schema.model_validate(json.loads(text))


async def structured(model: str, system: str, user: str, schema: type[T], *,
                     effort: str = "medium", max_tokens: int = 8000, retries: int = 1) -> T:
    """One call that returns a validated `schema` instance. Retries once on refusal / bad output.
    Models named deepseek-* go to DeepSeek; everything else to Claude."""
    if model.startswith("deepseek"):
        last = None
        for _ in range(retries + 1):
            try:
                return await _deepseek_structured(model, system, user, schema, max_tokens)
            except BudgetExceeded:
                raise
            except Exception as e:  # bad JSON / validation / HTTP → retry once
                last = e
        raise RuntimeError(f"deepseek call failed: {type(last).__name__}: {last}")
    fmt = {"type": "json_schema", "schema": strict_schema(schema)}
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            resp = await _create(model, system, user, effort, max_tokens, fmt)
            text = next(b.text for b in resp.content if b.type == "text")
            return schema.model_validate(json.loads(text))
        except BudgetExceeded:
            raise
        except (RuntimeError, ValueError, StopIteration, json.JSONDecodeError) as e:
            last = e
    raise RuntimeError(f"structured call failed: {last}")


async def text(model: str, system: str, user: str, *, effort: str = "low", max_tokens: int = 4000) -> str:
    resp = await _create(model, system, user, effort, max_tokens, None)
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def gather_limited(coros, limit: int):
    """Run coroutines with at most `limit` in flight; exceptions are returned, not raised."""
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
