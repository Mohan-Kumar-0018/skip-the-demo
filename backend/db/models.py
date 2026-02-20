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
