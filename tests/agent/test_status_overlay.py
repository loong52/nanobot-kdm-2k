"""Regression coverage for PR1 transient failure status."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.hook import ToolAuditOutcome
from nanobot.agent.runner import AgentRunner
from nanobot.agent.status_overlay import (
    AGENT_STATUS_FAILURE_LEDGER_KEY,
    FailureLedgerHook,
    begin_logical_user_request,
    build_failure_overlay,
    record_tool_terminal,
)
from nanobot.agent.tools.base import ToolResult
from nanobot.audit.context import AuditRunContext
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.providers.openai_codex_provider import OpenAICodexProvider
from nanobot.session.manager import SessionManager
from tests.agent.runner_helpers import make_run_spec


def _failure(*, source_event_id: str = "event-1") -> ToolAuditOutcome:
    return ToolAuditOutcome(
        status="error",
        result=ToolResult.error("Error: missing file"),
        error_type="ToolError",
        error_code="file_not_found",
        source_event_id=source_event_id,
    )


def test_failure_ledger_uses_exact_operation_fingerprints_and_clears_on_success() -> None:
    metadata: dict = {}
    message_metadata: dict = {}
    begin_logical_user_request(message_metadata, metadata)

    assert record_tool_terminal(
        metadata,
        message_metadata,
        tool_name="read_file",
        tool=None,
        params={"path": "missing-a.txt"},
        outcome=_failure(source_event_id="event-a1"),
    )
    assert record_tool_terminal(
        metadata,
        message_metadata,
        tool_name="read_file",
        tool=None,
        params={"path": "missing-b.txt"},
        outcome=_failure(source_event_id="event-b1"),
    )
    assert record_tool_terminal(
        metadata,
        message_metadata,
        tool_name="read_file",
        tool=None,
        params={"path": "missing-a.txt"},
        outcome=_failure(source_event_id="event-a2"),
    )

    ledger = metadata[AGENT_STATUS_FAILURE_LEDGER_KEY]
    assert len(ledger["failures"]) == 2
    overlay = build_failure_overlay(metadata, message_metadata)
    assert overlay is not None
    assert "same-operation failures=2" in overlay.content
    assert "missing-a.txt" not in overlay.content
    assert "missing-b.txt" not in overlay.content

    assert record_tool_terminal(
        metadata,
        message_metadata,
        tool_name="read_file",
        tool=None,
        params={"path": "missing-a.txt"},
        outcome=ToolAuditOutcome(status="ok", result="contents", source_event_id="event-a3"),
    )
    assert build_failure_overlay(metadata, message_metadata) is None


def test_new_logical_user_request_resets_failure_ledger() -> None:
    metadata: dict = {}
    first_message: dict = {}
    begin_logical_user_request(first_message, metadata)
    for index in range(2):
        record_tool_terminal(
            metadata,
            first_message,
            tool_name="read_file",
            tool=None,
            params={"path": "missing.txt"},
            outcome=_failure(source_event_id=f"event-{index}"),
        )
    assert build_failure_overlay(metadata, first_message) is not None

    second_message: dict = {}
    begin_logical_user_request(second_message, metadata)

    assert metadata[AGENT_STATUS_FAILURE_LEDGER_KEY]["revision"] == 0
    assert metadata[AGENT_STATUS_FAILURE_LEDGER_KEY]["failures"] == {}
    assert build_failure_overlay(metadata, second_message) is None


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events = []
        self.payloads = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        self.payloads.append(payload)
        return MagicMock(committed=critical)


@pytest.mark.asyncio
async def test_codex_overlay_is_transient_and_audited_without_status_body(tmp_path) -> None:
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("cli:status")
    message_metadata: dict = {}
    begin_logical_user_request(message_metadata, session.metadata)
    provider = OpenAICodexProvider()
    calls: list[list[dict]] = []

    async def chat_with_retry(*, messages, **_kwargs):
        calls.append(deepcopy(messages))
        if len(calls) < 3:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id=f"call-{len(calls)}",
                    name="read_file",
                    arguments={"path": "missing.txt"},
                )],
            )
        return LLMResponse(content="used a different approach")

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {"path": "missing.txt"}, None)
    tools.execute = AsyncMock(return_value=ToolResult.error("Error: missing file"))
    emitter = _RecordingEmitter()

    result = await AgentRunner(audit_emitter=emitter).run(make_run_spec(
        provider,
        initial_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "inspect"},
        ],
        tools=tools,
        model="openai-codex/gpt-5.6-sol",
        max_iterations=3,
        max_tool_result_chars=10_000,
        hook=FailureLedgerHook(
            session=session,
            sessions=sessions,
        ),
        status_overlay_factory=lambda: build_failure_overlay(session.metadata, None),
        session_key=session.key,
        audit_context=AuditRunContext("trace", "turn", "run"),
    ))

    assert result.final_content == "used a different approach"
    assert len(calls) == 3
    assert not any(message.get("role") == "developer" for message in calls[1])
    assert calls[2][-1]["role"] == "developer"
    assert "same-operation failures=2" in calls[2][-1]["content"]
    assert not any(message.get("role") == "developer" for message in result.messages)
    assert all("[Agent Status]" not in str(message) for message in result.messages)

    ledger = session.metadata[AGENT_STATUS_FAILURE_LEDGER_KEY]
    source_ids = next(iter(ledger["failures"].values()))["source_event_ids"]
    tool_finished_ids = [
        event.event_id for event in emitter.events if event.event_type == "tool_finished"
    ]
    assert source_ids == tool_finished_ids

    model_payloads = [
        payload for event, payload in zip(emitter.events, emitter.payloads)
        if event.event_type == "model_request_started"
    ]
    assert model_payloads[-1].content.agent_status["overlay_result"] == "applied"
    assert all(
        "[Agent Status]" not in str(message)
        for message in model_payloads[-1].content.messages
    )
