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
                   comments, design_links,
                   task_summary, pending_subtasks)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    ticket_title       = EXCLUDED.ticket_title,
                    ticket_description = EXCLUDED.ticket_description,
                    staging_url        = EXCLUDED.staging_url,
                    ticket_status      = EXCLUDED.ticket_status,
                    assignee           = EXCLUDED.assignee,
                    subtasks           = EXCLUDED.subtasks,
                    attachments        = EXCLUDED.attachments,
                    comments           = EXCLUDED.comments,
                    design_links       = EXCLUDED.design_links,
                    task_summary       = EXCLUDED.task_summary,
                    pending_subtasks   = EXCLUDED.pending_subtasks
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
                    json.dumps(data.get("design_links", [])),
                    data.get("task_summary", ""),
                    json.dumps(data.get("pending_subtasks", [])),
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
                  (run_id, figma_url, file_name, file_last_modified,
                   node_name, exported_images, export_errors)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    figma_url          = EXCLUDED.figma_url,
                    file_name          = EXCLUDED.file_name,
                    file_last_modified = EXCLUDED.file_last_modified,
                    node_name          = EXCLUDED.node_name,
                    exported_images    = EXCLUDED.exported_images,
                    export_errors      = EXCLUDED.export_errors
                """,
                (
                    run_id,
                    data.get("figma_url", ""),
                    data.get("file_name", ""),
                    data.get("file_last_modified", ""),
                    data.get("node_name", ""),
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


def get_dashboard_overview() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    r.id              AS job_id,
                    r.ticket_id,
                    r.feature_name,
                    r.status,
                    r.created_at,
                    r.completed_at,
                    EXTRACT(EPOCH FROM (r.completed_at - r.created_at))::int
                                      AS duration_secs,
                    COALESCE(rr.design_score, 0)   AS design_score,
                    COALESCE(rr.slack_sent, false)  AS slack_sent,
                    rr.video_path IS NOT NULL       AS has_video,
                    t.total_cost_usd,
                    t.total_tokens
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                LEFT JOIN (
                    SELECT run_id,
                           ROUND(SUM(cost_usd)::numeric, 4)  AS total_cost_usd,
                           SUM(input_tokens + output_tokens)  AS total_tokens
                    FROM run_token_usage
                    GROUP BY run_id
                ) t ON r.id = t.run_id
                ORDER BY r.created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_aggregate() -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Run counts and score from runs table (no fan-out)
            cur.execute(
                """
                SELECT
                    COUNT(*)                                       AS total_runs,
                    COUNT(*) FILTER (WHERE r.status = 'completed') AS completed,
                    COUNT(*) FILTER (WHERE r.status = 'failed')    AS failed,
                    COUNT(*) FILTER (WHERE r.status = 'running')   AS running,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE r.status = 'completed')
                        / NULLIF(COUNT(*), 0), 1
                    )                                              AS success_rate_pct,
                    COALESCE(
                        ROUND(AVG(rr.design_score)
                              FILTER (WHERE rr.design_score > 0)), 0
                    )                                              AS avg_design_score
                FROM runs r
                LEFT JOIN run_results rr ON r.id = rr.run_id
                """
            )
            stats = dict(cur.fetchone())

            # Token totals from separate query to avoid fan-out
            cur.execute(
                """
                SELECT
                    COALESCE(ROUND(SUM(cost_usd)::numeric, 4), 0)         AS total_cost_usd,
                    COALESCE(SUM(input_tokens + output_tokens)::bigint, 0) AS total_tokens
                FROM run_token_usage
                """
            )
            tokens = dict(cur.fetchone())
            stats.update(tokens)
            return stats


def get_dashboard_cost_breakdown() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    agent_name,
                    COUNT(*)                          AS call_count,
                    SUM(input_tokens)                 AS total_input,
                    SUM(output_tokens)                AS total_output,
                    ROUND(SUM(cost_usd)::numeric, 4)  AS total_cost,
                    ROUND(
                        100.0 * SUM(cost_usd)
                        / NULLIF(SUM(SUM(cost_usd)) OVER (), 0), 1
                    )                                 AS pct_of_total
                FROM run_token_usage
                GROUP BY agent_name
                ORDER BY total_cost DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_step_reliability() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    step_name,
                    COUNT(*)                                          AS total,
                    COUNT(*) FILTER (WHERE status = 'done')           AS succeeded,
                    COUNT(*) FILTER (WHERE status = 'failed')         AS failed,
                    COUNT(*) FILTER (WHERE status = 'pending')        AS pending,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE status = 'failed')
                        / NULLIF(COUNT(*), 0), 1
                    )                                                 AS failure_rate_pct
                FROM run_steps
                GROUP BY step_name
                ORDER BY failure_rate_pct DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_step_durations() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    rp.run_id,
                    rp.step_name,
                    rp.agent,
                    rp.status,
                    EXTRACT(EPOCH FROM (rp.completed_at - rp.started_at))::int
                        AS duration_secs,
                    rp.error
                FROM run_steps rp
                ORDER BY rp.run_id, rp.step_order
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_failures() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    r.id          AS job_id,
                    r.ticket_id,
                    r.feature_name,
                    r.created_at,
                    rp.step_name,
                    rp.error,
                    rp.agent
                FROM runs r
                JOIN run_steps rp
                    ON r.id = rp.run_id AND rp.status = 'failed'
                ORDER BY r.created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_funnel() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    r.id          AS job_id,
                    r.ticket_id,
                    r.feature_name,
                    r.status,
                    COUNT(*) FILTER (WHERE rp.status = 'done')  AS steps_completed,
                    COUNT(*)                                     AS steps_total,
                    MAX(CASE WHEN rp.status = 'failed'
                             THEN rp.step_name END)              AS failed_at_step
                FROM runs r
                JOIN run_steps rp ON r.id = rp.run_id
                GROUP BY r.id, r.ticket_id, r.feature_name, r.status
                ORDER BY r.created_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


# ── STEP OUTPUTS ─────────────────────────


def save_step_output(run_id: str, step_name: str, outputs: dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_step_outputs (run_id, step_name, outputs)
                VALUES (%s, %s, %s)
                ON CONFLICT (run_id, step_name)
                DO UPDATE SET outputs = EXCLUDED.outputs
                """,
                (run_id, step_name, json.dumps(outputs)),
            )


def get_step_output(run_id: str, step_name: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT outputs FROM run_step_outputs WHERE run_id=%s AND step_name=%s",
                (run_id, step_name),
            )
            row = cur.fetchone()
            if not row:
                return None
            outputs = row["outputs"]
            return json.loads(outputs) if isinstance(outputs, str) else outputs


def get_all_step_outputs(run_id: str) -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step_name, outputs FROM run_step_outputs WHERE run_id=%s",
                (run_id,),
            )
            result = {}
            for row in cur.fetchall():
                outputs = row["outputs"]
                result[row["step_name"]] = (
                    json.loads(outputs) if isinstance(outputs, str) else outputs
                )
            return result


def assemble_results(run_id: str) -> dict[str, Any]:
    """Build the final run_results shape from all step outputs in the DB."""
    outputs = get_all_step_outputs(run_id)

    jira_out = outputs.get("jira_fetch", {})
    browser_out = outputs.get("discover_crawl", outputs.get("browser_crawl", {}))
    vision_out = outputs.get("design_compare", {})
    synthesis_out = outputs.get("synthesis", {})
    slack_out = outputs.get("slack_delivery", {})

    return {
        "design_score": vision_out.get("design_score", 0),
        "deviations": vision_out.get("deviations", []),
        "summary": synthesis_out.get("summary", ""),
        "release_notes": synthesis_out.get("release_notes", ""),
        "video_path": browser_out.get("video_path"),
        "screenshots": browser_out.get("screenshots", []),
        "slack_sent": slack_out.get("slack_sent", False),
    }


# ── PLAN ─────────────────────────────────


def save_plan(run_id: str, steps: list[dict[str, Any]]) -> None:
    """Write the LLM-generated plan as JSONB into runs.plan (the intent)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET plan = %s WHERE id = %s",
                (json.dumps(steps), run_id),
            )


def get_plan_intent(run_id: str) -> list[dict[str, Any]]:
    """Read the raw LLM plan from runs.plan JSONB."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT plan FROM runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            if not row or not row["plan"]:
                return []
            plan = row["plan"]
            return json.loads(plan) if isinstance(plan, str) else plan


def get_plan(run_id: str) -> list[dict[str, Any]]:
    """Merge intent (runs.plan) + reality (run_steps rows).

    Returns the same shape as before so all callers work unchanged:
    each step dict has step_order, step_name, agent, params, depends_on,
    status, result_summary, error, started_at, completed_at.
    """
    intent = get_plan_intent(run_id)
    if not intent:
        return []

    # Fetch reality rows
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM run_steps WHERE run_id = %s",
                (run_id,),
            )
            reality = {r["step_name"]: dict(r) for r in cur.fetchall()}

    merged: list[dict[str, Any]] = []
    for step in intent:
        name = step["step_name"]
        if name in reality:
            merged.append(reality[name])
        else:
            # Step not yet executed — synthesize from intent
            merged.append({
                "id": None,
                "run_id": run_id,
                "step_order": step.get("step_order", 0),
                "step_name": name,
                "agent": step.get("agent", ""),
                "params": step.get("params", {}),
                "depends_on": step.get("depends_on", []),
                "status": "pending",
                "result_summary": None,
                "error": None,
                "started_at": None,
                "completed_at": None,
                "created_at": None,
            })

    return merged


def update_plan_step(
    run_id: str,
    step_name: str,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> None:
    """UPSERT into run_steps: INSERT on first touch (from intent data), UPDATE thereafter.

    "running" and "skipped" can both be first-touch statuses.
    """
    intent = get_plan_intent(run_id)
    intent_step = next((s for s in intent if s["step_name"] == step_name), None)

    with get_conn() as conn:
        with conn.cursor() as cur:
            if status == "running":
                cur.execute(
                    """
                    INSERT INTO run_steps
                      (run_id, step_order, step_name, agent, params, depends_on,
                       status, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id, step_name) DO UPDATE SET
                        status     = EXCLUDED.status,
                        started_at = NOW()
                    """,
                    (
                        run_id,
                        intent_step["step_order"] if intent_step else 0,
                        step_name,
                        intent_step["agent"] if intent_step else "",
                        json.dumps(intent_step.get("params", {})) if intent_step else "{}",
                        intent_step.get("depends_on", []) if intent_step else [],
                        status,
                    ),
                )
            else:
                # done, failed, skipped — may be first touch (e.g. skipped)
                cur.execute(
                    """
                    INSERT INTO run_steps
                      (run_id, step_order, step_name, agent, params, depends_on,
                       status, result_summary, error, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id, step_name) DO UPDATE SET
                        status         = EXCLUDED.status,
                        result_summary = EXCLUDED.result_summary,
                        error          = EXCLUDED.error,
                        completed_at   = NOW()
                    """,
                    (
                        run_id,
                        intent_step["step_order"] if intent_step else 0,
                        step_name,
                        intent_step["agent"] if intent_step else "",
                        json.dumps(intent_step.get("params", {})) if intent_step else "{}",
                        intent_step.get("depends_on", []) if intent_step else [],
                        status,
                        result_summary,
                        error,
                    ),
                )
