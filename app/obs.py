"""Sentry setup and the span helpers every agent and tool call goes through.

Gemini calls are traced automatically by Sentry's Google Gen AI integration.
Nimble has no integration, so each Nimble call is wrapped in a
`gen_ai.execute_tool` span here, and each pipeline in a `gen_ai.invoke_agent`
span. Together they show up as one agent trace in Sentry.

The same calls are also kept in a small in-memory log so the page can show
agent activity even when Sentry is not configured.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator

import sentry_sdk

from . import config

_LOG = logging.getLogger("nibble")
_LOG.setLevel(logging.INFO)
_ACTIVITY: deque[dict] = deque(maxlen=200)
_COUNTS = {"nimble": 0, "gemini": 0, "local": 0, "hosted": 0, "feed": 0, "errors": 0}


def init() -> None:
    if not config.SENTRY_LIVE:
        return
    from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration

    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        environment=config.ENVIRONMENT,
        traces_sample_rate=1.0,
        # Needed for prompts and model output to appear on AI spans.
        send_default_pii=config.SEND_PROMPTS_TO_SENTRY,
        integrations=[GoogleGenAIIntegration(include_prompts=config.SEND_PROMPTS_TO_SENTRY)],
        # Sentry Logs: every agent and tool call below is also written as a log line.
        enable_logs=True,
        # Profiling, attached to traces.
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
    )


def _short(value: Any, limit: int = 1500) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def log(kind: str, name: str, ms: float, ok: bool, detail: str = "", agent: str = "") -> None:
    if kind in _COUNTS:
        _COUNTS[kind] += 1
    if not ok:
        _COUNTS["errors"] += 1
    (_LOG.info if ok else _LOG.warning)("%s %s %s in %d ms%s", agent or "-", name, "ok" if ok else "FAILED", ms,
                                        "" if ok else f": {detail[:160]}")
    _ACTIVITY.appendleft(
        {
            "t": time.time(),
            "kind": kind,
            "name": name,
            "agent": agent,
            "ms": round(ms),
            "ok": ok,
            "detail": detail[:200],
        }
    )


def activity(limit: int = 40) -> dict:
    return {"counts": dict(_COUNTS), "events": list(_ACTIVITY)[:limit]}


@contextmanager
def agent_span(agent: str, task: str = "") -> Iterator[Any]:
    """One pipeline run (Basket Scout, Price Detective, ...)."""
    with sentry_sdk.start_span(op="gen_ai.invoke_agent", name=f"invoke_agent {agent}") as span:
        span.set_data("gen_ai.operation.name", "invoke_agent")
        span.set_data("gen_ai.agent.name", agent)
        if task:
            span.set_data(
                "gen_ai.input.messages",
                json.dumps([{"role": "user", "parts": [{"type": "text", "content": task[:2000]}]}]),
            )
        yield span


@contextmanager
def tool_span(tool: str, args: dict, agent: str = "", description: str = "") -> Iterator[Any]:
    """One Nimble call (or public-feed fetch) made on behalf of an agent."""
    start = time.perf_counter()
    kind = "nimble" if tool.startswith("nimble.") else "feed"
    with sentry_sdk.start_span(op="gen_ai.execute_tool", name=f"execute_tool {tool}") as span:
        span.set_data("gen_ai.operation.name", "execute_tool")
        span.set_data("gen_ai.tool.name", tool)
        span.set_data("gen_ai.tool.type", "function")
        if agent:
            span.set_data("gen_ai.agent.name", agent)
        if description:
            span.set_data("gen_ai.tool.description", description)
        span.set_data("gen_ai.tool.call.arguments", _short(args))
        try:
            yield span
        except Exception as exc:
            span.set_status("internal_error")
            span.set_data("error.type", type(exc).__name__)
            log(kind, tool, (time.perf_counter() - start) * 1000, False, str(exc), agent)
            raise
        else:
            log(kind, tool, (time.perf_counter() - start) * 1000, True, _short(args, 120), agent)


def set_result(span: Any, result: Any) -> None:
    span.set_data("gen_ai.tool.call.result", _short(result))


def capture(exc: BaseException) -> str | None:
    """Send an exception to Sentry and return its event id (None if Sentry is off)."""
    return sentry_sdk.capture_exception(exc) if config.SENTRY_LIVE else None
