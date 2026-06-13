"""Shared mock-construction helpers for the LLM-client test suites.

Each classifier/curator suite mocks the Anthropic client the same way:
build a real anthropic.types.Message holding one tool_use block, then
hand it to a MagicMock client. Per-service wrappers supply the tool
name, model, and payload shape.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from anthropic.types import Message, ToolUseBlock, Usage


def make_tool_use_block(
    *,
    tool_name: str,
    input_payload: Any,
    block_id: str = "toolu_test_01",
) -> ToolUseBlock:
    """Build a ToolUseBlock while bypassing pydantic validation on input.

    The real API can return input as a stringified JSON object even though
    the typed schema says dict; model_construct skips validation so tests
    can inject a string directly.
    """
    return ToolUseBlock.model_construct(
        type="tool_use",
        id=block_id,
        name=tool_name,
        input=input_payload,
    )


def make_mock_response(
    *,
    tool_name: str,
    input_payload: dict[str, Any],
    model: str,
    stringify_input: bool = False,
) -> Message:
    """Build a real anthropic.types.Message with one tool_use block.

    stringify_input=True wraps the tool input in a JSON string instead of
    a real dict, exercising the defensive parse path.
    """
    tool_block: ToolUseBlock
    if stringify_input:
        tool_block = make_tool_use_block(
            tool_name=tool_name, input_payload=json.dumps(input_payload)
        )
    else:
        tool_block = ToolUseBlock(
            type="tool_use",
            id="toolu_test_01",
            name=tool_name,
            input=input_payload,
        )
    return Message(
        id="msg_test_01",
        type="message",
        role="assistant",
        model=model,
        content=[tool_block],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=10),
    )


def make_mock_client(response: Message) -> MagicMock:
    """Return a MagicMock Anthropic client whose messages.create returns response."""
    client = MagicMock()
    client.messages.create.return_value = response
    return client
