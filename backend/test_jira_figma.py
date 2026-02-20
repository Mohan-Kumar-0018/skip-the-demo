"""Test Jira + Figma agents independently.

Usage: python test_jira_figma.py SG-238
"""
import asyncio
import json
import os
import re
import sys
import uuid

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


async def main():
    from agents.jira_agent import run_jira_agent
    from agents.figma_agent import run_figma_agent
    from db.models import (
        create_run, save_jira_data, save_figma_data,
        get_jira_data, get_figma_data,
        save_token_usage, get_token_usage, get_token_usage_summary,
    )
    from utils.pdf_parser import extract_text

    ticket_id = sys.argv[1] if len(sys.argv) > 1 else "SG-238"
    run_id = uuid.uuid4().hex[:8]

    console.print(Panel(
        f"[bold]Ticket:[/bold] {ticket_id}    [bold]Run ID:[/bold] {run_id}",
        title="[bold purple]Jira + Figma Test[/bold purple]",
        border_style="purple",
    ))

    create_run(run_id, ticket_id)

    # ── 1. Jira Agent ────────────────────────
    console.print("\n[bold cyan]Running Jira Agent...[/bold cyan]")
    jira_task = (
        f"Fetch all information for Jira ticket {ticket_id}.\n"
        f"Output directory for attachments: outputs/{run_id}/\n"
        "Get the ticket details, subtasks, attachments (download them), and comments."
    )
    os.makedirs(f"outputs/{run_id}", exist_ok=True)

    jira_result = await run_jira_agent(jira_task)
    jira_data = jira_result["data"]
    ticket = jira_data.get("ticket", {})

    # Save token usage
    usage = jira_result.get("usage", {})
    if usage:
        save_token_usage(run_id, "jira", usage.get("model", ""), usage.get("input_tokens", 0), usage.get("output_tokens", 0), usage.get("cost_usd", 0))
        console.print(f"  [dim]Jira tokens: {usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out — ${usage.get('cost_usd', 0):.4f}[/dim]")

    # Extract PRD
    prd_text = ""
    for att in jira_data.get("attachments", []):
        if att.get("category") == "prd" and att.get("path", "").endswith(".pdf"):
            if os.path.isfile(att["path"]):
                with open(att["path"], "rb") as f:
                    prd_text = extract_text(f.read())
                break

    # Extract Figma links
    figma_pattern = r'https?://(?:www\.)?figma\.com/(?:design|file)/[^\s\)\]\"\'>]+'
    design_links = []
    desc_str = str(ticket.get("description", ""))
    design_links.extend(re.findall(figma_pattern, desc_str))
    for comment in jira_data.get("comments", []):
        design_links.extend(re.findall(figma_pattern, comment.get("body", "")))
    design_links = list(set(design_links))

    # Save to DB
    save_jira_data(run_id, {
        "ticket_title": ticket.get("title", ""),
        "ticket_description": desc_str,
        "staging_url": ticket.get("staging_url", ""),
        "ticket_status": ticket.get("status", ""),
        "assignee": ticket.get("assignee", ""),
        "subtasks": jira_data.get("subtasks", []),
        "attachments": jira_data.get("attachments", []),
        "comments": jira_data.get("comments", []),
        "prd_text": prd_text,
        "design_links": design_links,
    })

    console.print("[green]Jira agent done.[/green]")

    # ── Print Jira results ────────────────────
    jira_db = get_jira_data(run_id)
    if jira_db:
        d = dict(jira_db)
        t = Table(title="Jira Data (from DB)", show_lines=True, border_style="cyan")
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
        prd_len = len(d.get("prd_text") or "")
        t.add_row("PRD Text", f"{prd_len} chars" if prd_len else "[red]not found[/red]")
        if prd_len:
            t.add_row("", f"  [dim]{str(d['prd_text'])[:200]}...[/dim]")
        links = d.get("design_links") or []
        t.add_row("Design Links", f"{len(links)} link(s)")
        for link in links:
            t.add_row("", f"  {link}")
        console.print(t)

    # ── 2. Figma Agent (if links found) ───────
    if design_links:
        console.print(f"\n[bold magenta]Running Figma Agent for {len(design_links)} link(s)...[/bold magenta]")
        for link in design_links:
            console.print(f"  [dim]{link}[/dim]")
            figma_task = (
                f"Extract design images from this Figma link: {link}\n"
                f"Output directory: outputs/{run_id}/\n"
                "Export all design screens as PNG images."
            )
            figma_result = await run_figma_agent(figma_task)
            figma_data = figma_result["data"]
            parsed = figma_data.get("parsed_url", {})
            file_info = figma_data.get("file_info", {})
            node_info = figma_data.get("node_info", {})

            # Save token usage
            fusage = figma_result.get("usage", {})
            if fusage:
                save_token_usage(run_id, "figma", fusage.get("model", ""), fusage.get("input_tokens", 0), fusage.get("output_tokens", 0), fusage.get("cost_usd", 0))
                console.print(f"  [dim]Figma tokens: {fusage.get('input_tokens', 0):,} in / {fusage.get('output_tokens', 0):,} out — ${fusage.get('cost_usd', 0):.4f}[/dim]")

            save_figma_data(run_id, {
                "figma_url": link,
                "file_key": parsed.get("file_key", ""),
                "node_id": parsed.get("node_id", ""),
                "file_name": file_info.get("name", ""),
                "file_last_modified": file_info.get("last_modified", ""),
                "pages": file_info.get("pages", []),
                "node_name": node_info.get("name", ""),
                "node_type": node_info.get("type", ""),
                "node_children": node_info.get("children", []),
                "exported_images": figma_data.get("exported", []),
                "export_errors": figma_data.get("errors", []),
            })

        console.print("[green]Figma agent done.[/green]")

        # Print Figma results
        figma_db = get_figma_data(run_id)
        if figma_db:
            d = dict(figma_db)
            t = Table(title="Figma Data (from DB)", show_lines=True, border_style="magenta")
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
        console.print("\n[dim]No Figma links found in ticket — skipping Figma agent[/dim]")

    # ── 3. Token Usage Summary ────────────────
    console.print()
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

    console.print(f"\n[bold green]Done![/bold green] Run ID: {run_id}\n")


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("pipeline.log", mode="w"),
        ],
    )
    logging.getLogger().handlers[0].setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.INFO)
    asyncio.run(main())
