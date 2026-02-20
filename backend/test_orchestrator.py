"""Rich terminal UI for testing the orchestrator pipeline.

Usage: python test_orchestrator.py SG-238
"""
import asyncio
import json
import logging
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.columns import Columns

console = Console()

# ── State for the live display ───────────────────────────
state = {
    "ticket_id": "",
    "run_id": "",
    "status": "starting",
    "stage": "",
    "progress": 0,
    "current_agent": "",
    "steps": {},
    "tool_log": [],       # list of (agent, tool_name, status, detail)
    "agent_thinking": "",  # latest text from agent
}

MAX_LOG_LINES = 20


# ── Custom log handler to capture agent events ──────────
class RichStateHandler(logging.Handler):
    def emit(self, record):
        msg = record.getMessage()
        name = record.name

        if "Orchestrator calling:" in msg:
            agent = msg.split("Orchestrator calling:")[-1].strip()
            state["current_agent"] = agent
            state["tool_log"].append(("orchestrator", agent, "calling", ""))

        elif "Orchestrator progress:" in msg:
            parts = msg.split("Orchestrator progress:")[-1].strip()
            state["tool_log"].append(("orchestrator", "progress", "update", parts))

        elif "Tool call:" in msg:
            tool_info = msg.split("Tool call:")[-1].strip()
            # e.g. get_jira_ticket({"ticket_id": "SG-238"})
            tool_name = tool_info.split("(")[0].strip()
            tool_input = tool_info[len(tool_name):].strip()[:80]
            agent = state["current_agent"] or "unknown"
            state["tool_log"].append((agent, tool_name, "running", tool_input))

        elif "Agent turn" in msg:
            pass  # skip noisy turn logs

        elif "Tool" in msg and "failed" in msg:
            state["tool_log"].append((state["current_agent"], "error", "failed", msg[:80]))

        # Keep log bounded
        if len(state["tool_log"]) > MAX_LOG_LINES:
            state["tool_log"] = state["tool_log"][-MAX_LOG_LINES:]


# ── Build the live display ────────────────────────────────
STEP_ICONS = {
    "pending": "[dim]○[/dim]",
    "running": "[yellow]◉[/yellow]",
    "done": "[green]●[/green]",
}

STEP_LABELS = {
    "jira_fetch": "Jira Fetch",
    "prd_parse": "PRD Parse",
    "browser_crawl": "Browser Crawl",
    "design_compare": "Design Compare",
    "synthesis": "Synthesis",
    "slack_delivery": "Slack Delivery",
}


def build_display():
    # ── Header
    header = Text(f"  SkipTheDemo Pipeline — {state['ticket_id']}  (run: {state['run_id']})", style="bold white on purple")

    # ── Progress steps
    steps_table = Table(show_header=False, box=None, padding=(0, 1))
    steps_table.add_column(width=3)
    steps_table.add_column(width=18)
    steps_table.add_column(width=10)
    for step_name in ["jira_fetch", "prd_parse", "browser_crawl", "design_compare", "synthesis", "slack_delivery"]:
        status = state["steps"].get(step_name, "pending")
        icon = STEP_ICONS.get(status, "○")
        label = STEP_LABELS.get(step_name, step_name)
        style = "green" if status == "done" else "yellow" if status == "running" else "dim"
        steps_table.add_row(icon, f"[{style}]{label}[/{style}]", f"[{style}]{status}[/{style}]")

    # ── Progress bar
    pct = state["progress"]
    bar_width = 40
    filled = int(bar_width * pct / 100)
    bar = f"[green]{'█' * filled}[/green][dim]{'░' * (bar_width - filled)}[/dim] {pct}%"

    # ── Tool call log
    log_lines = []
    for agent, tool, status, detail in state["tool_log"][-15:]:
        if status == "calling":
            log_lines.append(f"  [cyan]▸[/cyan] [bold]{tool}[/bold]")
        elif status == "running":
            short_detail = detail[:60] if detail else ""
            log_lines.append(f"    [dim]↳[/dim] [yellow]{tool}[/yellow] {short_detail}")
        elif status == "failed":
            log_lines.append(f"    [red]✗ {tool}: {detail}[/red]")
        elif status == "update":
            log_lines.append(f"  [blue]↻[/blue] {detail}")

    log_text = "\n".join(log_lines) if log_lines else "  [dim]waiting...[/dim]"

    # ── Current stage
    stage_text = state.get("stage", "") or "Initializing..."

    # ── Assemble
    steps_panel = Panel(steps_table, title="Pipeline Steps", border_style="blue")
    progress_panel = Panel(f"  {bar}\n  [dim]{stage_text}[/dim]", title="Progress", border_style="blue")
    log_panel = Panel(log_text, title="Tool Calls", border_style="blue", height=min(20, len(log_lines) + 4))

    layout = Table.grid(padding=1)
    layout.add_row(header)
    layout.add_row(Columns([steps_panel, progress_panel], equal=True))
    layout.add_row(log_panel)

    return layout


# ── Monkey-patch orchestrator to update state in real-time ─
def patch_orchestrator():
    """Wrap update_run and upsert_step to feed state dict."""
    from db import models as m
    _orig_update = m.update_run
    _orig_upsert = m.upsert_step

    def patched_update(run_id, stage, progress, status="running", feature_name=None):
        state["stage"] = stage
        state["progress"] = progress
        state["status"] = status
        return _orig_update(run_id, stage, progress, status, feature_name)

    def patched_upsert(run_id, step_name, step_status):
        state["steps"][step_name] = step_status
        return _orig_upsert(run_id, step_name, step_status)

    m.update_run = patched_update
    m.upsert_step = patched_upsert


# ── Print DB results after pipeline ──────────────────────
def print_results(run_id):
    from db.models import (
        get_browser_data,
        get_figma_data,
        get_jira_data,
        get_results,
        get_run,
        get_token_usage,
        get_token_usage_summary,
    )

    console.print()

    # Jira data
    jira = get_jira_data(run_id)
    if jira:
        d = dict(jira)
        t = Table(title="Jira Data", show_lines=True, border_style="cyan")
        t.add_column("Field", style="bold", width=16)
        t.add_column("Value")
        t.add_row("Title", str(d.get("ticket_title", "")))
        t.add_row("Status", str(d.get("ticket_status", "")))
        t.add_row("Assignee", str(d.get("assignee", "")))
        t.add_row("Staging URL", str(d.get("staging_url", "")))
        subtasks = d.get("subtasks") or []
        t.add_row("Subtasks", f"{len(subtasks)} subtask(s)")
        for st in subtasks:
            t.add_row("", f"  {st.get('key')}: {st.get('summary')} [{st.get('status')}]")
        attachments = d.get("attachments") or []
        t.add_row("Attachments", f"{len(attachments)} file(s)")
        for att in attachments:
            t.add_row("", f"  [{att.get('category')}] {att.get('filename')}")
        comments = d.get("comments") or []
        t.add_row("Comments", f"{len(comments)} comment(s)")
        for c in comments[:3]:
            body = str(c.get("body", ""))[:100]
            t.add_row("", f"  {c.get('author')}: {body}")
        prd_len = len(d.get("prd_text") or "")
        t.add_row("PRD Text", f"{prd_len} chars" if prd_len else "[red]not found[/red]")
        if prd_len:
            t.add_row("", f"  [dim]{str(d['prd_text'])[:200]}...[/dim]")
        links = d.get("design_links") or []
        t.add_row("Design Links", f"{len(links)} link(s)")
        for link in links:
            t.add_row("", f"  {link}")
        console.print(t)
    else:
        console.print("[red]No Jira data found in DB[/red]")

    console.print()

    # Figma data
    figma = get_figma_data(run_id)
    if figma:
        d = dict(figma)
        t = Table(title="Figma Data", show_lines=True, border_style="magenta")
        t.add_column("Field", style="bold", width=16)
        t.add_column("Value")
        t.add_row("File Key", str(d.get("file_key", "")))
        t.add_row("Node ID", str(d.get("node_id", "")))
        t.add_row("File Name", str(d.get("file_name", "")))
        t.add_row("Node Name", str(d.get("node_name", "")))
        t.add_row("Node Type", str(d.get("node_type", "")))
        children = d.get("node_children") or []
        t.add_row("Children", f"{len(children)} node(s)")
        images = d.get("exported_images") or []
        t.add_row("Exported", f"{len(images)} image(s)")
        for img in images:
            t.add_row("", f"  {img.get('name')}: {img.get('path')}")
        errors = d.get("export_errors") or []
        if errors:
            t.add_row("Errors", f"[red]{len(errors)} error(s)[/red]")
        console.print(t)
    else:
        console.print("[dim]No Figma data (no Figma link found in ticket)[/dim]")

    console.print()

    # Browser data
    browser = get_browser_data(run_id)
    if browser:
        d = dict(browser)
        t = Table(title="Browser Data", show_lines=True, border_style="green")
        t.add_column("Field", style="bold", width=16)
        t.add_column("Value")
        urls = d.get("urls_visited") or []
        t.add_row("URLs Visited", f"{len(urls)} page(s)")
        for u in urls:
            t.add_row("", f"  {u.get('title')}: {u.get('url')}")
        screenshots = d.get("screenshot_paths") or []
        t.add_row("Screenshots", f"{len(screenshots)} file(s)")
        for s in screenshots:
            t.add_row("", f"  {s}")
        t.add_row("Video", str(d.get("video_path") or "[red]none[/red]"))
        content_len = len(d.get("page_content") or "")
        t.add_row("Page Content", f"{content_len} chars")
        elements = d.get("interactive_elements") or []
        t.add_row("Elements", f"{len(elements)} interactive element(s)")
        console.print(t)
    else:
        console.print("[dim]No Browser data[/dim]")

    console.print()

    # Final results
    results = get_results(run_id)
    if results:
        d = dict(results)
        t = Table(title="Final Results", show_lines=True, border_style="yellow")
        t.add_column("Field", style="bold", width=16)
        t.add_column("Value")
        t.add_row("Design Score", str(d.get("design_score", "N/A")))
        t.add_row("Summary", str(d.get("summary") or "")[:300])
        t.add_row("Video", str(d.get("video_path") or "none"))
        t.add_row("Slack Sent", str(d.get("slack_sent")))
        console.print(t)

    # Token usage
    usage_rows = get_token_usage(run_id)
    if usage_rows:
        t = Table(title="Token Usage", show_lines=True, border_style="bright_blue")
        t.add_column("Agent", style="bold", width=14)
        t.add_column("Model", width=22)
        t.add_column("Input Tokens", justify="right", width=14)
        t.add_column("Output Tokens", justify="right", width=14)
        t.add_column("Cost (USD)", justify="right", width=12)
        for row in usage_rows:
            d = dict(row)
            t.add_row(
                d.get("agent_name", ""),
                d.get("model", ""),
                f"{d.get('input_tokens', 0):,}",
                f"{d.get('output_tokens', 0):,}",
                f"${float(d.get('cost_usd', 0)):.4f}",
            )
        summary = get_token_usage_summary(run_id)
        if summary:
            s = dict(summary)
            t.add_row(
                "[bold]TOTAL[/bold]", "",
                f"[bold]{int(s.get('total_input_tokens', 0)):,}[/bold]",
                f"[bold]{int(s.get('total_output_tokens', 0)):,}[/bold]",
                f"[bold]${float(s.get('total_cost_usd', 0)):.4f}[/bold]",
            )
        console.print(t)
    else:
        console.print("[dim]No token usage data[/dim]")

    console.print()

    run = get_run(run_id)
    if run:
        final_status = run.get("status", "unknown")
        style = "green" if final_status == "completed" else "red"
        console.print(f"\n  Pipeline finished: [{style}]{final_status}[/{style}]\n")


# ── Main ──────────────────────────────────────────────────
async def main():
    from db.models import create_run
    from orchestrator import run_pipeline

    ticket_id = sys.argv[1] if len(sys.argv) > 1 else "SG-238"
    run_id = uuid.uuid4().hex[:8]

    state["ticket_id"] = ticket_id
    state["run_id"] = run_id

    # Install hooks
    patch_orchestrator()
    handler = RichStateHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger("orchestrator").addHandler(handler)
    logging.getLogger("agent_runner").addHandler(handler)
    # Suppress default logging to keep the UI clean
    logging.getLogger("orchestrator").propagate = False
    logging.getLogger("agent_runner").propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)

    console.print(Panel(
        f"[bold]Ticket:[/bold] {ticket_id}    [bold]Run ID:[/bold] {run_id}",
        title="[bold purple]SkipTheDemo Pipeline Test[/bold purple]",
        border_style="purple",
    ))

    create_run(run_id, ticket_id)

    # Init steps
    for s in ["jira_fetch", "prd_parse", "browser_crawl", "design_compare", "synthesis", "slack_delivery"]:
        state["steps"][s] = "pending"

    # Run pipeline with live display
    with Live(build_display(), console=console, refresh_per_second=4) as live:
        async def refresh_loop():
            while state["status"] == "running" or state["status"] == "starting":
                live.update(build_display())
                await asyncio.sleep(0.25)

        refresh_task = asyncio.create_task(refresh_loop())

        try:
            state["status"] = "running"
            await run_pipeline(run_id, ticket_id)
            state["status"] = "completed"
            state["progress"] = 100
        except Exception as e:
            state["status"] = "failed"
            state["stage"] = f"Error: {e}"
            console.print(f"\n[red]Pipeline failed: {e}[/red]")
        finally:
            await asyncio.sleep(0.5)  # let final refresh happen
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

    # Show final DB results
    console.print()
    console.rule("[bold]Pipeline Results from DB[/bold]")
    print_results(run_id)


if __name__ == "__main__":
    # Console: warnings only (keeps Rich UI clean)
    # File: full debug logs including API errors
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),  # console — overridden below
            logging.FileHandler("pipeline.log", mode="w"),
        ],
    )
    logging.getLogger().handlers[0].setLevel(logging.WARNING)
    # Don't suppress httpx so rate-limit errors show in the log file
    logging.getLogger("httpx").setLevel(logging.INFO)
    asyncio.run(main())
