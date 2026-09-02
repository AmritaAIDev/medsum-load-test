"""Scheduled automatic test runs."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from medsum_testing.backend.routes.test_runner import _run_and_store
from medsum_testing.backend.services import medsum_api
from medsum_testing.backend.services.batch_identity import allocate_batch_identity
from medsum_testing.backend.services.config_loader import get_config, get_results_dir
from medsum_testing.backend.services.drive_service import list_test_cases
from medsum_testing.backend.services.result_store import has_recent_result

logger = logging.getLogger("medsum_scheduler")

_scheduler = BackgroundScheduler()
_schedule_lock = threading.Lock()
_run_lock_fh = None
_schedule_state: dict = {
    "enabled": False,
    "cron": "0 2 * * *",
    "last_run": None,
    "last_run_status": None,
    "next_run": None,
    "ai_model": "deepseek",
}


def _acquire_run_lock() -> bool:
    """Non-blocking exclusive lock so only one WSGI worker executes a scheduled suite."""
    global _run_lock_fh
    if _run_lock_fh is not None:
        return False
    lock_path = get_results_dir() / ".scheduler.lock"
    fh = None
    try:
        fh = open(lock_path, "a+b")
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b"":
                fh.write(b"\0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        _run_lock_fh = fh
        return True
    except OSError:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        return False


def _release_run_lock() -> None:
    global _run_lock_fh
    fh = _run_lock_fh
    _run_lock_fh = None
    if fh is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def run_all_tests(ai_model: str, skip_recent: bool = True) -> list[dict]:
    """Run all ready Drive test cases. Used by scheduler and manual trigger."""
    config = get_config()
    test_cases = [tc for tc in list_test_cases(config) if tc.get("status") == "ready"]

    logger.info("Scheduled run starting: %d test cases", len(test_cases))
    with _schedule_lock:
        _schedule_state["last_run"] = datetime.now(timezone.utc).isoformat()

    try:
        token, _ = medsum_api.authenticate_doctor(config)
    except Exception as exc:
        logger.error("Scheduled run auth failed: %s", exc)
        status = [{"status": "failed", "error": str(exc)}]
        with _schedule_lock:
            _schedule_state["last_run_status"] = status
        return status

    ident = allocate_batch_identity()
    batch_id = ident.batch_id
    medsum_api.create_batch(
        batch_id, ai_model, config, token, total_files=len(test_cases)
    )

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
        result = _run_and_store(
            test_id,
            tc["language"],
            audio,
            ai_model,
            batch_id,
            tc.get("folder_label", ""),
            "scheduler",
            token,
        )
        if result.status == "complete":
            return {"audio": audio, "status": "complete", "test_id": test_id}
        error = result.errors[0] if result.errors else "Test failed"
        return {"audio": audio, "status": "failed", "error": error, "test_id": test_id}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {executor.submit(_run_case, tc): tc for tc in test_cases}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    with _schedule_lock:
        _schedule_state["last_run_status"] = results
    logger.info("Scheduled run complete: %s", results)
    return results


def _cron_job(ai_model: str) -> None:
    """Cron callback: only one WSGI worker actually runs the suite."""
    if not _acquire_run_lock():
        logger.info(
            "Cron run skipped — another worker is already executing the scheduled suite"
        )
        return
    try:
        run_all_tests(ai_model)
    finally:
        _release_run_lock()


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
            func=lambda: _cron_job(ai_model),
            trigger=CronTrigger.from_crontab(cron),
            id="medsum_scheduled_run",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
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
