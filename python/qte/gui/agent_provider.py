"""Bounded text-only Agents API client for fixed, public research inputs."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from qte._cleanup import report_os_error
from .safeio import SafeReadError, read_registered_file

KEY_FILE = "build/qte-agents/.env.local"
MODEL = "gpt-6-astra"
API = "https://api.openai.com/v1/agents/sessions"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,150}\Z")
TEXT_LIMIT = 8192
OLLAMA_API = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
OLLAMA_REQUEST_LIMIT = 96 * 1024
OLLAMA_RESPONSE_LIMIT = 256 * 1024


class AgentProviderError(RuntimeError):
    def __init__(self, message: str, *, session_id=None):
        super().__init__(message)
        self.session_id = session_id


def read_agent_key(repository: Path) -> str | None:
    """Read the approved file without evaluating shell or exposing credentials."""
    try:
        contents = read_registered_file(repository, KEY_FILE).decode("utf-8")
    except (SafeReadError, UnicodeError):
        return None
    keys = re.findall(r"(?m)^(?:export\s+)?OPENAI_API_KEY=(sk-[A-Za-z0-9_-]{20,512})\r?$", contents)
    return keys[0] if len(keys) == 1 else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_ollama_model(model: object) -> str:
    if (type(model) is not str or OLLAMA_MODEL.fullmatch(model) is None
            or ".." in model or model.lower().endswith(("-cloud", ":cloud"))):
        raise ValueError("Ollama model must be a bounded local model name")
    return model


def consult_ollama(_key: None, instructions: str, context: str, *, model: str) -> dict:
    """Run one bounded, non-streaming chat against the fixed loopback Ollama API."""
    model = validate_ollama_model(model)
    if type(instructions) is not str or type(context) is not str:
        raise AgentProviderError("Local model request was invalid.")
    if len(instructions) > 32768 or len(context) > 65536:
        raise AgentProviderError("Local model request exceeded the research limit.")
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": context},
        ],
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {"num_predict": 1024},
    }, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > OLLAMA_REQUEST_LIMIT:
        raise AgentProviderError("Local model request exceeded the research limit.")
    request = Request(OLLAMA_API, payload, {
        "Content-Type": "application/json", "Accept": "application/json",
    }, method="POST")
    try:
        with build_opener(ProxyHandler({}), _NoRedirect).open(request, timeout=95) as response:
            if response.headers.get_content_type() != "application/json":
                raise AgentProviderError("Local model response was not JSON.")
            raw = response.read(OLLAMA_RESPONSE_LIMIT + 1)
            if len(raw) > OLLAMA_RESPONSE_LIMIT:
                raise AgentProviderError("Local model response exceeded the research limit.")
        result = json.loads(raw)
        message = result.get("message") if isinstance(result, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if (not isinstance(result, dict) or result.get("done") is not True
                or not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(content, str) or not content or len(content) > TEXT_LIMIT):
            raise AgentProviderError("Local model returned an invalid completed response.")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise AgentProviderError(f"Local model API returned HTTP {code}.") from None
    except AgentProviderError:
        raise
    except (OSError, URLError, ValueError, TypeError, AttributeError, UnicodeError):
        raise AgentProviderError("Local model transport or response failed.") from None
    return {"text": content, "model": model, "session_id": None,
            "turn_id": None, "session_cleanup": "not_applicable"}


def _events(response):
    deadline = time.monotonic() + 95
    buffer = []
    size = 0
    while time.monotonic() < deadline:
        line = response.readline(131073)
        if not line:
            return
        size += len(line)
        if len(line) > 131072 or size > 2 * 1024 * 1024:
            raise AgentProviderError("Agent response exceeded the research limit.")
        if line.startswith(b"data:"):
            buffer.append(line[5:].strip())
        elif not line.strip() and buffer:
            event = json.loads(b"\n".join(buffer))
            buffer.clear()
            if not isinstance(event, dict):
                raise AgentProviderError("Agent returned an invalid event.")
            yield event
    raise AgentProviderError("Agent response timed out.")


def _failure_code(event: dict) -> str:
    for source in (event.get("error"), (event.get("turn") or {}).get("error"), event):
        if isinstance(source, dict):
            code = source.get("code")
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,60}", code):
                return f" ({code})"
    return ""


def consult(key: str, instructions: str, context: str) -> dict:
    """Run one text-only root turn. QTE owns all experiment execution."""
    opener = build_opener(_NoRedirect)
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Beta": "agents=v1"}
    payload = {
        "agent": {
            "model": MODEL, "instructions": instructions, "tools": [],
            "multi_agent": {"enabled": False},
            "text": {"verbosity": "low"},
        },
        "environment": {"type": "none"},
        "input": context,
        "stream": True,
    }
    request = Request(API, json.dumps(payload).encode(),
                      {**headers, "Content-Type": "application/json",
                       "Accept": "text/event-stream"}, method="POST")
    session_id = None
    turn_id = None
    text = {}
    complete = False
    failure = None
    cleanup = "unconfirmed"
    try:
        with opener.open(request, timeout=35) as response:
            if response.headers.get_content_type() != "text/event-stream":
                raise AgentProviderError("Agent response was missing its event stream.")
            for event in _events(response):
                value = event.get("session_id")
                if isinstance(value, str) and SESSION_ID.fullmatch(value):
                    session_id = value
                kind = event.get("type")
                if kind in {"error", "agent.session.failed", "agent.session.environment.failed",
                            "agent.session.requires_action"}:
                    raise AgentProviderError(f"Agent session did not complete{_failure_code(event)}.")
                turn = event.get("turn") or {}
                if kind in {"agent.session.turn.failed", "agent.session.turn.cancelled"} and not turn.get("subagent_id"):
                    raise AgentProviderError(f"Agent turn failed or was cancelled{_failure_code(event)}.")
                if kind == "agent.session.turn.output_text.done" and isinstance(event.get("text"), str):
                    part = (event.get("item_id"), event.get("output_index"), event.get("content_index"))
                    text[part] = event["text"][:TEXT_LIMIT]
                if kind == "agent.session.turn.completed" and not turn.get("subagent_id"):
                    turn_id = turn.get("id")
                    complete = True
                    break
        if not complete or not text:
            raise AgentProviderError("Agent stream ended without a completed text result.")
    except HTTPError as exc:
        failure = f"OpenAI Agents API returned HTTP {exc.code}."
        exc.close()
    except AgentProviderError as exc:
        failure = str(exc)
    except (OSError, URLError, ValueError, TypeError, AttributeError):
        failure = "Agent transport or response failed."
    finally:
        if session_id is not None:
            if not complete:
                cancel = Request(f"{API}/{session_id}/events",
                                 b'{"events":[{"type":"agent.session.input.cancel"}]}',
                                 {**headers, "Content-Type": "application/json"}, method="POST")
                try:
                    with opener.open(cancel, timeout=5):
                        pass
                except OSError as error:
                    report_os_error("Agent session cancellation", error)
            try:
                with opener.open(Request(f"{API}/{session_id}", headers=headers, method="DELETE"), timeout=5):
                    cleanup = "deleted"
            except OSError as error:
                report_os_error("Agent session deletion", error)
    if failure:
        raise AgentProviderError(failure, session_id=session_id if cleanup != "deleted" else None)
    return {"text": "\n".join(text.values())[:TEXT_LIMIT], "model": MODEL,
            "session_id": session_id, "turn_id": turn_id, "session_cleanup": cleanup}
