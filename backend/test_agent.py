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

AGENTS = ["jira", "browser", "vision", "synthesis", "slack", "figma", "nav_planner", "discover_crawl", "score_eval", "demo_video"]


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

    elif agent == "nav_planner":
        from agents.navigation_planner_agent import plan_navigation

        images_dir = os.environ.get("IMAGES_DIR", "")
        if not images_dir or not os.path.isdir(images_dir):
            print("nav_planner agent requires IMAGES_DIR env var (directory with design PNGs).")
            print('Example: make test-nav IMAGES_DIR=outputs/23d8c274 PROMPT="Supplier discovery feature"')
            sys.exit(1)
        figma_images = [
            {"path": os.path.join(images_dir, f), "name": f}
            for f in sorted(os.listdir(images_dir))
            if f.startswith("figma") and f.endswith(".png")
        ]
        if not figma_images:
            print(f"No figma*.png files found in {images_dir}")
            sys.exit(1)
        print(f"  Images:  {len(figma_images)} PNGs from {images_dir}")
        print()
        raw = plan_navigation(figma_images, prompt)
        result = json.dumps(raw, indent=2)

    elif agent == "discover_crawl":
        from agents.discover_crawl_agent import run_discover_crawl

        kb_key = os.environ.get("KB_KEY")
        if not kb_key:
            print("discover_crawl agent requires KB_KEY env var.")
            print('Example: make test-discover KB_KEY=fina-customer-panel')
            sys.exit(1)

        figma_dir = os.environ.get("FIGMA_DIR") or None

        # Derive job_id from figma output directory (e.g. "outputs/23d8c274" -> "23d8c274")
        if figma_dir:
            job_id = os.path.basename(figma_dir.rstrip("/"))
        else:
            job_id = os.environ.get("JOB_ID", "")
            if not job_id:
                print("discover_crawl agent requires FIGMA_DIR or JOB_ID env var.")
                print('Example: make test-discover KB_KEY=fina-customer-panel FIGMA_DIR=outputs/23d8c274')
                sys.exit(1)

        print(f"  Job ID:    {job_id}")
        print(f"  KB Key:    {kb_key}")
        if figma_dir:
            print(f"  Figma Dir: {figma_dir}")
        print()

        raw = await run_discover_crawl(job_id, kb_key, figma_dir)
        crawl_data = raw.get("data", {}).get("crawl", {}).get("data", {})
        result = json.dumps(
            {
                "crawl_result": raw.get("summary", {}),
                "navigation_flows": raw.get("data", {}).get("navigation", {}).get("flows", []),
                "screenshots": crawl_data.get("screenshot_paths", []),
                "video": crawl_data.get("video_path"),
                "usage": raw.get("usage", {}),
            },
            indent=2,
        )

    elif agent == "score_eval":
        from agents.score_evaluator_agent import evaluate_scores

        uat_dir = os.environ.get("UAT_DIR", "")
        figma_dir = os.environ.get("FIGMA_DIR", "")
        if not uat_dir or not os.path.isdir(uat_dir):
            print("score_eval agent requires UAT_DIR env var (directory with UAT screenshot PNGs).")
            print('Example: make test-score-eval UAT_DIR=outputs/uat_screenshots FIGMA_DIR=outputs/23d8c274')
            sys.exit(1)
        if not figma_dir or not os.path.isdir(figma_dir):
            print("score_eval agent requires FIGMA_DIR env var (directory with Figma design PNGs).")
            print('Example: make test-score-eval UAT_DIR=outputs/uat_screenshots FIGMA_DIR=outputs/23d8c274')
            sys.exit(1)
        uat_count = len([f for f in os.listdir(uat_dir) if f.lower().endswith(".png")])
        figma_count = len([f for f in os.listdir(figma_dir) if f.lower().endswith(".png")])
        print(f"  UAT Dir:   {uat_dir} ({uat_count} PNGs)")
        print(f"  Figma Dir: {figma_dir} ({figma_count} PNGs)")
        print()
        raw = evaluate_scores(uat_dir, figma_dir)
        result = json.dumps(raw, indent=2)

    elif agent == "demo_video":
        from agents.demo_video_agent import generate_demo_video

        video = os.environ.get("VIDEO", "")
        action_log_path = os.environ.get("ACTION_LOG", "")
        screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "")
        feature = os.environ.get("FEATURE", "")

        if not video or not os.path.exists(video):
            print("demo_video agent requires VIDEO env var (path to .webm or .mov file).")
            print('Example: make test-demo-video VIDEO=outputs/uat_screenshots/abc.webm ACTION_LOG=outputs/uat_screenshots/action_log.json')
            print('        make test-demo-video VIDEO=path/to/recording.mov ACTION_LOG=outputs/uat_screenshots/action_log.json')
            sys.exit(1)

        # Load action log
        action_log = []
        if action_log_path and os.path.exists(action_log_path):
            with open(action_log_path) as f:
                action_log = json.load(f)
        else:
            print("Warning: No ACTION_LOG provided. Narration may be limited.")

        # Collect screenshot paths
        screenshot_paths = []
        if screenshots_dir and os.path.isdir(screenshots_dir):
            screenshot_paths = sorted([
                os.path.join(screenshots_dir, f)
                for f in os.listdir(screenshots_dir)
                if f.lower().endswith(".png")
            ])

        print(f"  Video:        {video}")
        print(f"  Action Log:   {action_log_path} ({len(action_log)} entries)")
        print(f"  Screenshots:  {len(screenshot_paths)} PNGs")
        if feature:
            print(f"  Feature:      {feature}")
        print()

        raw = await generate_demo_video(
            video_path=video,
            action_log=action_log,
            screenshot_paths=screenshot_paths or None,
            feature_context=feature,
        )
        result = json.dumps(raw, indent=2)

    else:
        print(f"Unknown agent: {agent}. Choose from: {', '.join(AGENTS)}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  {agent.upper()} AGENT RESULT")
    print(f"{'='*60}\n")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
