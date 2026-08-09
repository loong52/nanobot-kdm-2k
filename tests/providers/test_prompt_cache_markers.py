from __future__ import annotations

from typing import Any

from nanobot.agent.context import ContextBuilder
from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.openai_compat_provider import OpenAICompatProvider


def _openai_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _anthropic_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": f"{name} tool",
            "input_schema": {"type": "object", "properties": {}},
        }
        for name in names
    ]


def _marked_openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    marked: list[str] = []
    for tool in tools:
        if "cache_control" in tool:
            marked.append((tool.get("function") or {}).get("name", ""))
    return marked


def _marked_anthropic_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    return [tool.get("name", "") for tool in tools if "cache_control" in tool]


def test_openai_compat_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_anthropic_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    _, _, marked_tools = AnthropicProvider._apply_cache_control(
        "system",
        messages,
        _anthropic_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_anthropic_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_anthropic_wires_stable_system_prefix_before_dynamic_facts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    builder = ContextBuilder(workspace)
    builder.memory.append_history("recent fact")
    messages = builder.build_messages([], "inspect")
    provider = object.__new__(AnthropicProvider)
    provider.default_model = "claude-test"
    provider.extra_headers = {}
    kwargs = provider._build_kwargs(
        messages,
        tools=None,
        model="claude-test",
        max_tokens=100,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )

    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert "# Recent History" not in system[0]["text"]
    assert "# Recent History" in system[1]["text"]
    assert "_nanobot_stable_prefix" not in str(system)


def test_openai_compat_strips_section_metadata_without_changing_tool_or_image_wire() -> None:
    provider = object.__new__(OpenAICompatProvider)
    provider.default_model = "test-model"
    provider._spec = None
    provider._extra_body = {}
    messages = [
        {
            "role": "system",
            "content": "stable\n\n---\n\ndynamic",
            "_meta": {"system_context_sections": {"schema_version": 1}},
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                {"type": "text", "text": "inspect"},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"note.txt"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "read_file", "content": "ok"},
    ]
    kwargs = provider._build_kwargs(
        messages,
        tools=_openai_tools("read_file"),
        model="test-model",
        max_tokens=100,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )

    wire = kwargs["messages"]
    assert all("_meta" not in message for message in wire)
    assert wire[1]["content"][0]["type"] == "image_url"
    assert wire[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert wire[3]["role"] == "tool"


def test_openai_compat_marks_only_tail_without_mcp() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file"]
