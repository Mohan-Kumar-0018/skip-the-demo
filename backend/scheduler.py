from __future__ import annotations

import asyncio
import logging
from typing import Any

from db.models import (
    assemble_results,
    complete_run,
    fail_run,
    get_plan,
    save_results,
    update_run,
)
from executor import CRITICAL_STEPS, STEP_LABELS, run_step
from planner import create_plan, replan

logger = logging.getLogger(__name__)


class PipelineScheduler:
    """Event-driven scheduler that dispatches steps as background tasks
    and calls the LLM replanner after each step completes."""

    def __init__(self, run_id: str, ticket_id: str) -> None:
        self.run_id = run_id
        self.ticket_id = ticket_id
        self._lock = asyncio.Lock()
        self._done = asyncio.Event()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._failed_critical = False

    async def start(self) -> None:
        """Main entry point: create plan, then drive the event loop."""
        # Phase 1: LLM generates full plan
        await create_plan(self.run_id, self.ticket_id)

        # Phase 2: Ask what to dispatch first
        decision = await replan(self.run_id, self.ticket_id)

        if decision["action"] == "complete":
            # Edge case: plan has no actionable steps
            await self._complete_pipeline()
            return

        if decision["action"] == "dispatch":
            self._dispatch_steps(decision["steps"])

        # Wait until the pipeline signals completion
        await self._done.wait()

    def _dispatch_steps(self, step_names: list[str]) -> None:
        """Fire background tasks for each step, skipping already-running ones."""
        plan = get_plan(self.run_id)
        step_map = {s["step_name"]: s for s in plan}

        for name in step_names:
            if name in self._running_tasks:
                logger.warning("Step %s already running, skipping dispatch", name)
                continue

            step = step_map.get(name)
            if not step:
                logger.warning("Step %s not found in plan, skipping", name)
                continue

            task = asyncio.create_task(
                self._run_step_with_callback(step),
                name=f"step-{name}",
            )
            self._running_tasks[name] = task

        self._update_progress()

    async def _run_step_with_callback(self, step: dict[str, Any]) -> None:
        """Execute a single step, then trigger the replan callback."""
        step_name = step["step_name"]
        try:
            await run_step(self.run_id, self.ticket_id, step)
        except Exception:
            # Critical step failure — abort the whole pipeline
            if step_name in CRITICAL_STEPS:
                await self._abort(f"Critical step {step_name} failed")
                return
        finally:
            self._running_tasks.pop(step_name, None)

        if not self._failed_critical:
            await self._on_step_done()

    async def _abort(self, reason: str) -> None:
        """Cancel all running tasks and mark the pipeline as failed."""
        async with self._lock:
            if self._done.is_set():
                return
            self._failed_critical = True
            # Cancel sibling tasks before signalling completion
            for name, task in self._running_tasks.items():
                if not task.done():
                    logger.info("Cancelling step %s due to abort: %s", name, reason)
                    task.cancel()
            fail_run(self.run_id, reason)
            self._done.set()

    async def _on_step_done(self) -> None:
        """Replan callback — serialized via lock to prevent double-dispatch."""
        async with self._lock:
            if self._done.is_set():
                return

            self._update_progress()

            try:
                decision = await replan(self.run_id, self.ticket_id)
            except Exception:
                logger.exception("Replan failed for run %s, falling back to deterministic check", self.run_id)
                decision = self._deterministic_replan()

            if decision["action"] == "complete":
                await self._complete_pipeline()
            elif decision["action"] == "dispatch":
                self._dispatch_steps(decision["steps"])
            # "wait" — do nothing, a running task will trigger the next callback

    def _deterministic_replan(self) -> dict[str, Any]:
        """Fallback replan using the same rules as the LLM prompt, no API call."""
        plan = get_plan(self.run_id)
        satisfies_dep = {"done", "skipped"}  # "failed" does NOT satisfy
        ready = []
        any_running = False
        for step in plan:
            if step["status"] == "running":
                any_running = True
            elif step["status"] == "pending":
                deps = step.get("depends_on", [])
                dep_statuses = {
                    s["step_name"]: s["status"] for s in plan if s["step_name"] in deps
                }
                if all(dep_statuses.get(d) in satisfies_dep for d in deps):
                    ready.append(step["step_name"])
        if ready:
            return {"action": "dispatch", "steps": ready}
        if any_running:
            return {"action": "wait", "steps": []}
        return {"action": "complete", "steps": []}

    async def _complete_pipeline(self) -> None:
        """Assemble results from DB, save, and signal completion."""
        results = assemble_results(self.run_id)
        save_results(self.run_id, results)
        plan = get_plan(self.run_id)
        failed_steps = [s["step_name"] for s in plan if s["status"] == "failed"]
        if failed_steps:
            fail_run(self.run_id, f"Steps failed: {', '.join(failed_steps)}")
            logger.info("Pipeline finished with failures for run %s: %s", self.run_id, failed_steps)
        else:
            complete_run(self.run_id)
            logger.info("Pipeline completed for run %s", self.run_id)
        self._done.set()

    def _update_progress(self) -> None:
        """Compute progress from step counts and update the run."""
        plan = get_plan(self.run_id)
        total = len(plan)
        if total == 0:
            return
        completed = sum(
            1 for s in plan if s["status"] in ("done", "skipped", "failed")
        )
        progress = int((completed / total) * 100)
        # Find a running step label for the stage display
        running = [s["step_name"] for s in plan if s["status"] == "running"]
        if running:
            stage = STEP_LABELS.get(running[0], f"Running {running[0]}...")
        else:
            stage = "Processing..."
        update_run(self.run_id, stage, progress)
