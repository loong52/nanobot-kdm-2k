"""Transient, request-scoped Agent Status facts.

The rendered status is deliberately never stored.  Only the small failure
ledger lives in session metadata so a continuation can retain the logical
user-request boundary after a process restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Mapping, MutableMapping

from nanobot.agent.hook import AgentHook, AgentHookContext, ToolAuditOutcome
from nanobot.audit.diagnostics import tool_operation_evidence
from nanobot.audit.ids import new_audit_id

if TYPE_CHECKING:
    from nanobot.session.manager import Session, SessionManager


AGENT_STATUS_FAILURE_LEDGER_KEY = "_agent_status_failure_ledger"
LOGICAL_USER_REQUEST_ID_META = "_logical_user_request_id"
STATUS_SCHEMA_VERSION = 1
_MAX_SOURCE_EVENT_IDS = 8
_MAX_RENDERED_FAILURES = 4

OverlayResult = Literal["applied", "omitted_unsupported", "omitted_invalid"]


@dataclass(frozen=True, slots=True)
class AgentStatusOverlay:
    """Bounded, model-only status with audit-safe metadata."""

    content: str
    status_revision: int
    source_event_ids: tuple[str, ...]
    generated_at: str
    scope: str = "logical_request"

    def audit_metadata(self, result: OverlayResult) -> dict[str, Any]:
        return {
            "status_revision": self.status_revision,
            "status_schema_version": STATUS_SCHEMA_VERSION,
            "source_event_ids": list(self.source_event_ids),
            "generated_at": self.generated_at,
            "scope": self.scope,
            "overlay_result": result,
        }


def begin_logical_user_request(
    message_metadata: MutableMapping[str, Any],
    session_metadata: MutableMapping[str, Any],
) -> str:
    """Atomically replace a session ledger for a real inbound user request."""
    request_id = new_audit_id()
    message_metadata[LOGICAL_USER_REQUEST_ID_META] = request_id
    session_metadata[AGENT_STATUS_FAILURE_LEDGER_KEY] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "logical_user_request_id": request_id,
        "revision": 0,
        "failures": {},
    }
    return request_id


def inherit_logical_user_request(
    message_metadata: Mapping[str, Any] | None,
    session_metadata: Mapping[str, Any] | None,
) -> str | None:
    """Return a continuation's request id only when it matches persisted state."""
    request_id = (
        message_metadata.get(LOGICAL_USER_REQUEST_ID_META)
        if isinstance(message_metadata, Mapping)
        else None
    )
    ledger = _validated_ledger(session_metadata)
    if ledger is None:
        return None
    if request_id is None:
        return ledger["logical_user_request_id"]
    if not isinstance(request_id, str) or not request_id:
        return None
    return request_id if ledger["logical_user_request_id"] == request_id else None


def record_tool_terminal(
    session_metadata: MutableMapping[str, Any],
    message_metadata: Mapping[str, Any] | None,
    *,
    tool_name: str,
    tool: Any,
    params: Any,
    outcome: ToolAuditOutcome,
) -> bool:
    """Persist one terminal operation outcome in the active logical request.

    A missing or unsafe fingerprint is intentionally ignored: counting it
    would create an imprecise retry signal.  Cancellation is retained as an
    interrupted outcome but never counted as a retryable failure.
    """
    request_id = inherit_logical_user_request(message_metadata, session_metadata)
    if request_id is None:
        return False
    evidence = tool_operation_evidence(tool_name, tool, params)
    if not evidence.retry_key:
        return False
    parsed = _validated_ledger(session_metadata)
    if parsed is None:
        return False
    ledger = parsed
    failures = ledger["failures"]
    previous = failures.get(evidence.retry_key)
    source_event_id = outcome.source_event_id
    status = _terminal_status(outcome)

    if status == "ok":
        failures.pop(evidence.retry_key, None)
    else:
        old_count = _non_negative_int((previous or {}).get("consecutive_failures"))
        failures[evidence.retry_key] = {
            "tool_name": str(tool_name)[:80],
            "consecutive_failures": old_count + 1 if status in {"error", "blocked", "timeout"} else 0,
            "last_error_class": _error_class(outcome),
            "last_outcome": status,
            "source_event_ids": _append_source_event_id(
                (previous or {}).get("source_event_ids"), source_event_id
            ),
            "updated_at": _utc_now(),
        }
    ledger["revision"] = _non_negative_int(ledger.get("revision")) + 1
    session_metadata[AGENT_STATUS_FAILURE_LEDGER_KEY] = ledger
    return True


def build_failure_overlay(
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None,
) -> AgentStatusOverlay | None:
    """Render the current repeated-failure facts without exposing raw inputs."""
    if inherit_logical_user_request(message_metadata, session_metadata) is None:
        return None
    ledger = _validated_ledger(session_metadata)
    if ledger is None:
        return None
    failures = []
    source_event_ids: list[str] = []
    for entry in ledger["failures"].values():
        if not isinstance(entry, Mapping):
            continue
        count = _non_negative_int(entry.get("consecutive_failures"))
        outcome = entry.get("last_outcome")
        name = entry.get("tool_name")
        if count < 2 or outcome not in {"error", "blocked", "timeout"} or not isinstance(name, str):
            continue
        failures.append((name, count, _safe_error_class(entry.get("last_error_class")), entry))
    if not failures:
        return None
    failures.sort(key=lambda item: (-item[1], item[0]))
    lines = ["[Agent Status]"]
    for name, count, error_class, entry in failures[:_MAX_RENDERED_FAILURES]:
        lines.append(
            f"Repeated failure: {name} same-operation failures={count}; class={error_class}"
        )
        for event_id in entry.get("source_event_ids", []):
            if isinstance(event_id, str) and event_id and event_id not in source_event_ids:
                source_event_ids.append(event_id)
    lines.append("[/Agent Status]")
    content = "\n".join(lines)
    if len(content) > 1000:
        return None
    return AgentStatusOverlay(
        content=content,
        status_revision=_non_negative_int(ledger.get("revision")),
        source_event_ids=tuple(source_event_ids[:_MAX_SOURCE_EVENT_IDS]),
        generated_at=_utc_now(),
    )


class FailureLedgerHook(AgentHook):
    """Persist the ledger after a terminal tool audit event and before checkpointing."""

    def __init__(
        self,
        *,
        session: Session,
        sessions: SessionManager,
    ) -> None:
        super().__init__()
        self._session = session
        self._sessions = sessions

    async def after_execute_tool_terminal(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        outcome: ToolAuditOutcome,
    ) -> None:
        if record_tool_terminal(
            self._session.metadata,
            None,
            tool_name=str(getattr(tool_call, "name", "")),
            tool=tool,
            params=params,
            outcome=outcome,
        ):
            self._sessions.save(self._session)


def _validated_ledger(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(AGENT_STATUS_FAILURE_LEDGER_KEY)
    if not isinstance(raw, Mapping):
        return None
    request_id = raw.get("logical_user_request_id")
    failures = raw.get("failures")
    if raw.get("schema_version") != STATUS_SCHEMA_VERSION or not isinstance(request_id, str):
        return None
    if not isinstance(failures, Mapping):
        return None
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "logical_user_request_id": request_id,
        "revision": _non_negative_int(raw.get("revision")),
        "failures": {str(key): dict(value) for key, value in failures.items() if isinstance(value, Mapping)},
    }


def _terminal_status(outcome: ToolAuditOutcome) -> str:
    if outcome.status == "ok":
        return "ok"
    if outcome.status == "cancelled":
        return "interrupted"
    if outcome.status == "error" and (
        outcome.error_type in {"Timeout", "TimeoutError"}
        or outcome.error_kind in {"Timeout", "TimeoutError"}
    ):
        return "timeout"
    return outcome.status if outcome.status in {"error", "blocked", "timeout"} else "unknown"


def _error_class(outcome: ToolAuditOutcome) -> str:
    failure = outcome.failure
    return _safe_error_class(
        failure.error_code if failure and failure.error_code else (
            failure.error_type if failure else outcome.error_code or outcome.error_type or outcome.error_kind
        )
    )


def _safe_error_class(value: Any) -> str:
    text = str(value or "unknown")
    return "".join(char for char in text if char.isalnum() or char in {"_", "-"})[:80] or "unknown"


def _append_source_event_id(value: Any, source_event_id: str | None) -> list[str]:
    ids = [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []
    if source_event_id and source_event_id not in ids:
        ids.append(source_event_id)
    return ids[-_MAX_SOURCE_EVENT_IDS:]


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
