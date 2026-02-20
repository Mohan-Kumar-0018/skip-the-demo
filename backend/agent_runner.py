from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

import anthropic

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()

MODEL = "claude-sonnet-4-6"


async def run_agent_loop(
    system_prompt: str,
    tools: list[dict[str, Any]],
    tool_executor: Callable[[str, dict[str, Any]], Awaitable[Any]],
    user_message: str,
    max_turns: int = 15,
) -> str:
    """Shared agentic loop: send messages -> check for tool_use -> execute -> feed back -> repeat.

    Args:
        system_prompt: The system prompt for the agent.
        tools: List of Anthropic tool definitions.
        tool_executor: Async function that takes (tool_name, tool_input) and returns the result.
        user_message: The initial user message to send.
        max_turns: Safety limit to prevent infinite loops.

    Returns:
        The final text response from the agent.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        logger.info("Agent turn %d: stop_reason=%s", turn + 1, response.stop_reason)

        # If the model wants to use tools, execute them and continue
        if response.stop_reason == "tool_use":
            # Append the assistant's response (includes tool_use blocks)
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("  Tool call: %s(%s)", block.name, json.dumps(block.input)[:200])
                    try:
                        result = await tool_executor(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result) if not isinstance(result, str) else result,
                        })
                    except Exception as e:
                        logger.exception("Tool %s failed", block.name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": str(e)}),
                            "is_error": True,
                        })

            messages.append({"role": "user", "content": tool_results})
            continue

        # end_turn or max_tokens — extract final text and return
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        return "\n".join(text_parts) if text_parts else ""

    # Safety: if we hit max_turns, return whatever we have
    logger.warning("Agent hit max_turns (%d) safety limit", max_turns)
    return "Agent reached maximum number of turns without completing."
