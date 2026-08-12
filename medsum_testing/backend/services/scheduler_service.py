"""Scheduled automatic test runs."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from medsum_testing.backend.routes.test_runner import execute_test_run
from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.drive_service import list_test_cases
from medsum_testing.backend.services.result_store import has_recent_result

logger = logging.getLogger("medsum_scheduler")

_scheduler = BackgroundScheduler()
_schedule_lock = threading.Lock()
_schedule_state: dict = {
    "enabled": False,
    "cron": "0 2 * * *",
    "last_run": None,
    "last_run_status": None,
    "next_run": None,
    "ai_model": "deepseek",
}


def run_all_tests(ai_model: str, skip_recent: bool = True) -> list[dict]:
    """Run all ready Drive test cases. Used by scheduler and manual trigger."""
    config = get_config()
    test_cases = [tc for tc in list_test_cases(config) if tc.get("status") == "ready"]

    logger.info("Scheduled run starting: %d test cases", len(test_cases))
    _schedule_state["last_run"] = datetime.now(timezone.utc).isoformat()

    max_parallel = config.get("scheduler", {}).get("max_parallel_tests", 2)
    results: list[dict] = []

    def _run_case(tc: dict) -> dict:
        audio = tc["audio_filename"]
        if skip_recent and has_recent_result(audio, within_seconds=60):
            return {
                "audio": audio,
                "status": "skipped",
                "reason": "Result already exists from the last 60 seconds",
            }
        test_id = str(uuid.uuid4())
        result = execute_test_run(tc["language"], audio, ai_model, test_id=test_id)
        if result.status == "complete":
            return {"audio": audio, "status": "complete", "test_id": test_id}
        error = result.errors[0] if result.errors else "Test failed"
        return {"audio": audio, "status": "failed", "error": error, "test_id": test_id}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {executor.submit(_run_case, tc): tc for tc in test_cases}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    _schedule_state["last_run_status"] = results
    logger.info("Scheduled run complete: %s", results)
    return results


def start_scheduler(config: dict) -> None:
    global _scheduler

    sched_config = config.get("scheduler", {})
    if not sched_config.get("enabled", False):
        logger.info("Scheduler disabled in config")
        return

    cron = sched_config.get("cron", "0 2 * * *")
    ai_model = sched_config.get("ai_model", "deepseek")

    with _schedule_lock:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = BackgroundScheduler()

        _scheduler.add_job(
            func=lambda: run_all_tests(ai_model),
            trigger=CronTrigger.from_crontab(cron),
            id="medsum_scheduled_run",
            replace_existing=True,
        )
        _scheduler.start()

        job = _scheduler.get_job("medsum_scheduled_run")
        _schedule_state.update(
            {
                "enabled": True,
                "cron": cron,
                "ai_model": ai_model,
                "next_run": str(job.next_run_time) if job else None,
            }
        )
    logger.info("Scheduler started: %s", cron)


def stop_scheduler() -> None:
    with _schedule_lock:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
    _schedule_state["enabled"] = False
    _schedule_state["next_run"] = None


def restart_scheduler(config: dict) -> None:
    stop_scheduler()
    start_scheduler(config)


def get_schedule_state() -> dict:
    with _schedule_lock:
        if _scheduler.running:
            job = _scheduler.get_job("medsum_scheduled_run")
            if job:
                _schedule_state["next_run"] = str(job.next_run_time)
    return dict(_schedule_state)


def trigger_run_now(ai_model: str) -> dict:
    run_id = str(uuid.uuid4())

    def _background():
        run_all_tests(ai_model, skip_recent=True)

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()

    config = get_config()
    test_count = len(
        [tc for tc in list_test_cases(config) if tc.get("status") == "ready"]
    )
    return {"run_id": run_id, "status": "started", "test_count": test_count}
