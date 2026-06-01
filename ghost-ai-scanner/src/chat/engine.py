# =============================================================
# FILE: src/chat/engine.py
# VERSION: 2.1.0
# UPDATED: 2026-05-02
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: PatronAI chat engine — transport-agnostic tool-call loop.
#          Tools are rollup-backed and take (scope, scope_id) computed
#          once per turn from the current view + email + company.
#          The legacy `events` arg is accepted but ignored.
# DEPENDS: chat.tools, chat.prompts, chat.llm, chat.tools_schema,
#          query.rollup_reader (for scope resolution)
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial — in-memory events list.
#   v2.0.0  2026-04-29  Rollup-backed; scope/scope_id passed to tools.
#   v2.1.0  2026-05-02  Moved from dashboard/ui/chat/ to src/chat/.
#                       sys.path workaround removed — query/ is sibling.
# =============================================================

import json
import logging
import os

import requests

from .tools        import (get_summary_stats, get_top_risky_users,
                            get_user_risk_profile, query_findings,
                            get_fleet_status, get_shadow_ai_census,
                            get_recent_activity, compare_periods)
from .help         import get_help, refresh_docs
from .tools_schema import TOOLS_SCHEMA
from .prompts      import build_system_prompt
from .llm          import get_client

from query.rollup_reader import scope_for_view, resolve_scope_id  # noqa: E402

log = logging.getLogger("patronai.chat.engine")

_MAX_ROUNDS = 5  # tool-call loop guard


# ── Tool dispatch table ───────────────────────────────────────

# Tools that take (scope, scope_id, **kwargs).
_SCOPED_TOOLS = {
    "get_summary_stats":     get_summary_stats,
    "get_top_risky_users":   get_top_risky_users,
    "get_user_risk_profile": get_user_risk_profile,
    "query_findings":        query_findings,
    "get_fleet_status":      get_fleet_status,
    "get_shadow_ai_census":  get_shadow_ai_census,
    "get_recent_activity":   get_recent_activity,
    "compare_periods":       compare_periods,
}

# Tools that take **kwargs only (no scope).
_UNSCOPED_TOOLS = {
    "get_help":     get_help,
    "refresh_docs": refresh_docs,
}


def _run_tool(name: str, arguments: dict,
              scope: str, scope_id: str) -> str:
    """Execute one tool and return its result as a JSON string.
    Tools that need data context are passed (scope, scope_id) by the engine,
    not by the LLM — those are derived from the current view + email + company."""
    if name in _SCOPED_TOOLS:
        fn = _SCOPED_TOOLS[name]
        try:
            args = dict(arguments) if arguments else {}
            result = fn(scope, scope_id, **args)
            return json.dumps(result, default=str)
        except Exception as exc:
            log.warning("scoped tool %s failed: %s", name, exc)
            return json.dumps({"error": str(exc)})
    if name in _UNSCOPED_TOOLS:
        fn = _UNSCOPED_TOOLS[name]
        try:
            args = dict(arguments) if arguments else {}
            # get_help historically takes (events, topic) — pass an empty list
            # for backward compatibility; events is unused inside the function.
            result = fn([], **args)
            return json.dumps(result, default=str)
        except Exception as exc:
            log.warning("unscoped tool %s failed: %s", name, exc)
            return json.dumps({"error": str(exc)})
    return json.dumps({"error": f"unknown tool: {name}"})


# ── Public API ────────────────────────────────────────────────


def call_llm(messages: list, events: list, view: str,
             email: str, company: str) -> str:
    """Run a full chat turn: inject system prompt, execute tool loop,
    return the final assistant text string.

    Args:
        messages: Conversation history — [{"role":..., "content":...}, ...]
                  Already includes the latest user message.
        events:   Legacy — accepted for signature compatibility, IGNORED.
                  Tools now read from S3 rollups directly.
        view:     "exec" | "manager" | "support" | "home"
        email:    Logged-in user email.
        company:  Company name for system prompt.

    Raises:
        RuntimeError: If no LLM provider is reachable or config is invalid.
    """
    scope = scope_for_view(view)
    scope_id = resolve_scope_id(view, email, company)
    log.debug("chat scope: view=%s → scope=%s scope_id=%s",
              view, scope, scope_id[:8])

    sys_msg = {"role": "system",
               "content": build_system_prompt(scope, scope_id, view, email, company)}
    # Keep last 20 messages to stay within the 8192-token context window.
    all_msgs = [sys_msg] + messages[-20:]

    try:
        client = get_client()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    # Pre-flight: check llama-server is ready before sending a full request.
    _base = os.environ.get("LLM_BASE_URL", "http://localhost:8080").rstrip("/")
    try:
        hc = requests.get(f"{_base}/health", timeout=4)
        status = hc.json().get("status", "")
        if status != "ok":
            raise RuntimeError(
                "LLM is still loading the model — please wait a moment and try again.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            "LLM server is starting up — please wait a moment and try again.")
    except RuntimeError:
        raise
    except Exception:  # intentional: returns safe default on any error
        pass  # health endpoint absent (cloud API) — proceed

    try:
        for _round in range(_MAX_ROUNDS):
            result = client.complete(all_msgs, TOOLS_SCHEMA)

            if result["tool_calls"]:
                all_msgs.append(result["raw_msg"])
                for tc in result["tool_calls"]:
                    output = _run_tool(tc["name"], tc["arguments"],
                                       scope, scope_id)
                    log.debug("tool %s → %d chars", tc["name"], len(output))
                    all_msgs.append(
                        client.tool_result_msg(tc["id"], tc["name"], output))
                continue  # loop — LLM will now summarise tool results

            return result["content"] or "(empty response)"

        return "I reached the tool-call limit — please rephrase your question."

    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            "LLM server is unreachable — check LLM_BASE_URL.") from exc
    except Exception as exc:
        log.error("LLM call failed: %s", exc)
        raise RuntimeError(f"LLM error: {exc}") from exc
