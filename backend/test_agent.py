"""Universal agent test runner.

Usage: python test_agent.py <agent> "<prompt>"

Agents: jira, browser, vision, synthesis, slack, figma
"""
import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

AGENTS = ["jira", "browser", "vision", "synthesis", "slack", "figma"]


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

        kb_key = os.environ.get("KB_KEY")
        if kb_key:
            # Auto-build prompt from knowledge base
            from tools.kb_tools import get_knowledge

            kb_entry = get_knowledge("staging_urls", kb_key)
            if isinstance(kb_entry, dict) and "error" in kb_entry:
                print(f"KB lookup failed: {kb_entry['error']}")
                sys.exit(1)

            url = kb_entry["url"]
            creds = {k: v for k, v in kb_entry.items() if k != "url"}

            # Generate a job_id
            import hashlib, time

            job_id = f"test-auto-{hashlib.md5(f'{kb_key}{time.time()}'.encode()).hexdigest()[:6]}"

            creds_text = ""
            if creds:
                creds_text = "\n\nLogin credentials:\n" + "\n".join(
                    f"  {k}: {v}" for k, v in creds.items()
                )

            page_name = os.environ.get("PAGE")
            page_instruction = ""
            if page_name:
                page_instruction = (
                    f"\n\nTarget section: {page_name}\n"
                    f"After logging in, navigate to the '{page_name}' section/page "
                    f"and focus your exploration there. Discover and capture ALL "
                    f"functionalities within this section."
                )

            prompt = (
                f"Explore the web application at {url}\n"
                f"Job ID: {job_id}\n"
                f"Output directory: outputs/{job_id}/\n"
                f"{creds_text}"
                f"{page_instruction}\n\n"
                "Auto-discover and capture all functionalities on the page. "
                "Follow your exploration protocol to systematically interact with "
                "every UI element and take screenshots of each distinct state."
            )
            print(f"  Job ID:  {job_id}")
            print(f"  KB Key:  {kb_key}")
            if page_name:
                print(f"  Page:    {page_name}")
            print(f"  URL:     {url}")
            print()

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

    elif agent == "figma":
        from agents.figma_agent import run_figma_agent

        result = await run_figma_agent(prompt)

    else:
        print(f"Unknown agent: {agent}. Choose from: {', '.join(AGENTS)}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  {agent.upper()} AGENT RESULT")
    print(f"{'='*60}\n")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
