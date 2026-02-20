"""Universal agent test runner.

Usage: python test_agent.py <agent> "<prompt>"

Agents: jira, browser, vision, synthesis, slack
"""
import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

AGENTS = ["jira", "browser", "vision", "synthesis", "slack"]


async def main():
    if len(sys.argv) < 3:
        print(f"Usage: python test_agent.py <{'|'.join(AGENTS)}> \"<prompt>\"")
        sys.exit(1)

    agent = sys.argv[1]
    prompt = sys.argv[2]

    print(f"\n{'='*60}")
    print(f"  Testing: {agent.upper()} Agent")
    print(f"  Prompt:  {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
    print(f"{'='*60}\n")

    if agent == "jira":
        from agents.jira_agent import run_jira_agent

        result = await run_jira_agent(prompt)

    elif agent == "browser":
        from agents.browser_agent import run_browser_agent

        result = await run_browser_agent(prompt)

    elif agent == "vision":
        from agents.vision_agent import compare_design_vs_reality

        design = os.environ.get("DESIGN")
        screenshot = os.environ.get("SCREENSHOT")
        if not design or not screenshot:
            print("Vision agent requires DESIGN and SCREENSHOT env vars (file paths).")
            print('Example: make test-vision DESIGN=./design.png SCREENSHOT=./screen.png PROMPT="compare"')
            sys.exit(1)
        with open(design, "rb") as f:
            design_bytes = f.read()
        raw = compare_design_vs_reality(design_bytes, [screenshot])
        result = json.dumps(raw, indent=2)

    elif agent == "synthesis":
        from agents.synthesis_agent import generate_pm_summary

        feature = os.environ.get("FEATURE", "Test Feature")
        design_result = json.loads(
            os.environ.get(
                "DESIGN_RESULT",
                '{"score": 0, "deviations": [], "summary": "No design comparison available."}',
            )
        )
        raw = generate_pm_summary(feature, prompt, design_result)
        result = json.dumps(raw, indent=2)

    elif agent == "slack":
        from agents.slack_agent import run_slack_agent

        result = await run_slack_agent(prompt)

    else:
        print(f"Unknown agent: {agent}. Choose from: {', '.join(AGENTS)}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  {agent.upper()} AGENT RESULT")
    print(f"{'='*60}\n")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
