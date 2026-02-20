from __future__ import annotations

import json
from typing import Any

from db.connection import get_conn


# ── RUNS ──────────────────────────────────


def create_run(run_id: str, ticket_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs (id, ticket_id, status, stage, progress)
                VALUES (%s, %s, 'running', 'Starting...', 0)
                """,
                (run_id, ticket_id),
            )


def update_run(
    run_id: str,
    stage: str,
    progress: int,
    status: str = "running",
    feature_name: str | None = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runs
                SET stage=%s, progress=%s, status=%s,
                    feature_name=COALESCE(%s, feature_name)
                WHERE id=%s
                """,
                (stage, progress, status, feature_name, run_id),
            )


def complete_run(run_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runs
                SET status='completed', progress=100, completed_at=NOW()
                WHERE id=%s
                """,
                (run_id,),
            )


def fail_run(run_id: str, error: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status='failed', stage=%s WHERE id=%s",
                (f"Error: {error}", run_id),
            )


def get_run(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE id=%s", (run_id,))
            return cur.fetchone()


def get_all_runs() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, rr.design_score, rr.slack_sent
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                ORDER BY r.created_at DESC
                """
            )
            return cur.fetchall()


# ── STEPS ─────────────────────────────────


def upsert_step(run_id: str, step_name: str, step_status: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_steps (run_id, step_name, step_status, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (run_id, step_name)
                DO UPDATE SET step_status=%s, updated_at=NOW()
                """,
                (run_id, step_name, step_status, step_status),
            )


def get_steps(run_id: str) -> dict[str, str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT step_name, step_status FROM run_steps
                WHERE run_id=%s ORDER BY id
                """,
                (run_id,),
            )
            rows = cur.fetchall()
            return {r["step_name"]: r["step_status"] for r in rows}


# ── RESULTS ───────────────────────────────


def save_results(run_id: str, results: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_results
                  (run_id, design_score, deviations, summary,
                   release_notes, video_path, screenshots, slack_sent)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    design_score  = EXCLUDED.design_score,
                    deviations    = EXCLUDED.deviations,
                    summary       = EXCLUDED.summary,
                    release_notes = EXCLUDED.release_notes,
                    video_path    = EXCLUDED.video_path,
                    screenshots   = EXCLUDED.screenshots,
                    slack_sent    = EXCLUDED.slack_sent
                """,
                (
                    run_id,
                    results["design_score"],
                    json.dumps(results["deviations"]),
                    results["summary"],
                    results["release_notes"],
                    results["video_path"],
                    json.dumps(results["screenshots"]),
                    results["slack_sent"],
                ),
            )


def get_results(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, rr.design_score, rr.deviations, rr.summary,
                       rr.release_notes, rr.video_path, rr.screenshots,
                       rr.slack_sent
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                WHERE r.id=%s
                """,
                (run_id,),
            )
            return cur.fetchone()


# ── JIRA DATA ────────────────────────────


def save_jira_data(run_id: str, data: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_jira_data
                  (run_id, ticket_title, ticket_description, staging_url,
                   ticket_status, assignee, subtasks, attachments,
                   comments, prd_text, design_links)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    ticket_title       = EXCLUDED.ticket_title,
                    ticket_description = EXCLUDED.ticket_description,
                    staging_url        = EXCLUDED.staging_url,
                    ticket_status      = EXCLUDED.ticket_status,
                    assignee           = EXCLUDED.assignee,
                    subtasks           = EXCLUDED.subtasks,
                    attachments        = EXCLUDED.attachments,
                    comments           = EXCLUDED.comments,
                    prd_text           = EXCLUDED.prd_text,
                    design_links       = EXCLUDED.design_links
                """,
                (
                    run_id,
                    data.get("ticket_title", ""),
                    data.get("ticket_description", ""),
                    data.get("staging_url", ""),
                    data.get("ticket_status", ""),
                    data.get("assignee", ""),
                    json.dumps(data.get("subtasks", [])),
                    json.dumps(data.get("attachments", [])),
                    json.dumps(data.get("comments", [])),
                    data.get("prd_text", ""),
                    json.dumps(data.get("design_links", [])),
                ),
            )


def get_jira_data(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM run_jira_data WHERE run_id=%s",
                (run_id,),
            )
            return cur.fetchone()


# ── FIGMA DATA ───────────────────────────


def save_figma_data(run_id: str, data: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_figma_data
                  (run_id, figma_url, file_key, node_id, file_name,
                   file_last_modified, pages, node_name, node_type,
                   node_children, exported_images, export_errors)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    figma_url          = EXCLUDED.figma_url,
                    file_key           = EXCLUDED.file_key,
                    node_id            = EXCLUDED.node_id,
                    file_name          = EXCLUDED.file_name,
                    file_last_modified = EXCLUDED.file_last_modified,
                    pages              = EXCLUDED.pages,
                    node_name          = EXCLUDED.node_name,
                    node_type          = EXCLUDED.node_type,
                    node_children      = EXCLUDED.node_children,
                    exported_images    = EXCLUDED.exported_images,
                    export_errors      = EXCLUDED.export_errors
                """,
                (
                    run_id,
                    data.get("figma_url", ""),
                    data.get("file_key", ""),
                    data.get("node_id", ""),
                    data.get("file_name", ""),
                    data.get("file_last_modified", ""),
                    json.dumps(data.get("pages", [])),
                    data.get("node_name", ""),
                    data.get("node_type", ""),
                    json.dumps(data.get("node_children", [])),
                    json.dumps(data.get("exported_images", [])),
                    json.dumps(data.get("export_errors", [])),
                ),
            )


def get_figma_data(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM run_figma_data WHERE run_id=%s",
                (run_id,),
            )
            return cur.fetchone()


# ── BROWSER DATA ─────────────────────────


def save_browser_data(run_id: str, data: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_browser_data
                  (run_id, urls_visited, page_titles, screenshot_paths,
                   video_path, page_content, interactive_elements)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    urls_visited         = EXCLUDED.urls_visited,
                    page_titles          = EXCLUDED.page_titles,
                    screenshot_paths     = EXCLUDED.screenshot_paths,
                    video_path           = EXCLUDED.video_path,
                    page_content         = EXCLUDED.page_content,
                    interactive_elements = EXCLUDED.interactive_elements
                """,
                (
                    run_id,
                    json.dumps(data.get("urls_visited", [])),
                    json.dumps(data.get("page_titles", [])),
                    json.dumps(data.get("screenshot_paths", [])),
                    data.get("video_path", ""),
                    data.get("page_content", ""),
                    json.dumps(data.get("interactive_elements", [])),
                ),
            )


def get_browser_data(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM run_browser_data WHERE run_id=%s",
                (run_id,),
            )
            return cur.fetchone()


# ── TOKEN USAGE ─────────────────────────


def save_token_usage(
    run_id: str,
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_token_usage
                  (run_id, agent_name, model, input_tokens, output_tokens, cost_usd)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, agent_name, model, input_tokens, output_tokens, cost_usd),
            )


def get_token_usage(run_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent_name, model, input_tokens, output_tokens, cost_usd, created_at
                FROM run_token_usage
                WHERE run_id=%s
                ORDER BY created_at
                """,
                (run_id,),
            )
            return cur.fetchall()


def get_token_usage_summary(run_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                    COALESCE(SUM(cost_usd), 0) AS total_cost_usd
                FROM run_token_usage
                WHERE run_id=%s
                """,
                (run_id,),
            )
            return cur.fetchone()
