from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Awaitable

import anthropic

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(max_retries=5)

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


def calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost based on model pricing (per million tokens)."""
    PRICING = {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-sonnet-4-5-20241022": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    }
    rates = PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


async def run_agent_loop(
    system_prompt: str,
    tools: list[dict[str, Any]],
    tool_executor: Callable[[str, dict[str, Any]], Awaitable[Any]],
    user_message: str,
    max_turns: int = 15,
    model: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Shared agentic loop: send messages -> check for tool_use -> execute -> feed back -> repeat.

    Args:
        system_prompt: The system prompt for the agent.
        tools: List of Anthropic tool definitions.
        tool_executor: Async function that takes (tool_name, tool_input) and returns the result.
        user_message: The initial user message to send.
        max_turns: Safety limit to prevent infinite loops.
        model: Override model (defaults to CLAUDE_MODEL env var / claude-sonnet-4-6).
        verbose: If True, log full input prompt, agent text, and tool results without trimming.

    Returns:
        Dict with 'text' (final response) and 'usage' (token tracking).
    """
    use_model = model or MODEL
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    total_input_tokens = 0
    total_output_tokens = 0

    if verbose:
        logger.info("Agent start: model=%s, max_turns=%d, prompt=\n%s", use_model, max_turns, user_message)
    else:
        logger.info("Agent start: model=%s, max_turns=%d, prompt=%s", use_model, max_turns, user_message[:300])

    for turn in range(max_turns):
        response = client.messages.create(
            model=use_model,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        logger.info("Agent turn %d: stop_reason=%s, tokens=%d/%d", turn + 1, response.stop_reason, response.usage.input_tokens, response.usage.output_tokens)

        # Log any text the agent produced this turn (thinking/reasoning between tool calls)
        for block in response.content:
            if hasattr(block, "text") and block.text:
                logger.info("  Agent text: %s", block.text if verbose else block.text[:300])

        # If the model wants to use tools, execute them and continue
        if response.stop_reason == "tool_use":
            # Append the assistant's response (includes tool_use blocks)
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("  Tool call: %s(%s)", block.name, json.dumps(block.input) if verbose else json.dumps(block.input)[:200])
                    try:
                        result = await tool_executor(block.name, block.input)
                        result_str = json.dumps(result) if not isinstance(result, str) else result
                        logger.info("  Tool result: %s -> %s", block.name, result_str if verbose else result_str[:300])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
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
        usage = {
            "model": use_model,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_usd": calc_cost(use_model, total_input_tokens, total_output_tokens),
        }
        text = "\n".join(text_parts) if text_parts else ""
        logger.info("Agent done: turns=%d, tokens=%d/%d, response=%s", turn + 1, total_input_tokens, total_output_tokens, text if verbose else text[:200])
        return {"text": text, "usage": usage}

    # Safety: if we hit max_turns, return whatever we have
    last_prompt = str(messages[-1].get("content", "")) if verbose else (str(messages[-1].get("content", ""))[:200] if messages else "")
    logger.warning("Agent hit max_turns (%d) safety limit, last_prompt=%s", max_turns, last_prompt)
    usage = {
        "model": use_model,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cost_usd": calc_cost(use_model, total_input_tokens, total_output_tokens),
    }
    return {"text": "Agent reached maximum number of turns without completing.", "usage": usage}
