"""Scheduler API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from medsum_testing.backend.services.config_loader import (
    get_config,
    load_config,
    update_schedule_config,
)
from medsum_testing.backend.services.scheduler_service import (
    get_schedule_state,
    restart_scheduler,
    stop_scheduler,
    trigger_run_now,
)

bp = Blueprint("medsum_scheduler", __name__)

CRON_DESCRIPTIONS = {
    "0 2 * * *": "Every day at 2:00 AM",
    "0 8 * * *": "Every day at 8:00 AM",
    "0 8 * * 1-5": "Weekdays at 8:00 AM",
    "0 0 * * 0": "Every Sunday at midnight",
    "*/30 * * * *": "Every 30 minutes",
    "*/2 * * * *": "Every 2 minutes (testing)",
}


def cron_to_human(cron: str) -> str:
    return CRON_DESCRIPTIONS.get(cron, cron)


@bp.route("/schedule", methods=["GET"])
def get_schedule():
    state = get_schedule_state()
    config = get_config()
    sched = config.get("scheduler", {})
    return jsonify(
        {
            "enabled": state.get("enabled", sched.get("enabled", False)),
            "cron": state.get("cron", sched.get("cron", "0 2 * * *")),
            "cron_human": cron_to_human(state.get("cron", sched.get("cron", "0 2 * * *"))),
            "ai_model": state.get("ai_model", sched.get("ai_model", "deepseek")),
            "next_run": state.get("next_run"),
            "last_run": state.get("last_run"),
            "last_run_status": state.get("last_run_status"),
        }
    )


@bp.route("/schedule", methods=["POST"])
def update_schedule():
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))
    cron = body.get("cron", "0 2 * * *").strip()
    ai_model = body.get("ai_model", "deepseek").strip()

    VALID_MODELS = ("gpt-4o-mini", "gpt-4o", "gpt-4", "deepseek")
    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400

    update_schedule_config(enabled, cron, ai_model)
    config = load_config(force_reload=True)

    if enabled:
        restart_scheduler(config)
    else:
        stop_scheduler()

    state = get_schedule_state()
    return jsonify(
        {
            "enabled": enabled,
            "cron": cron,
            "cron_human": cron_to_human(cron),
            "ai_model": ai_model,
            "next_run": state.get("next_run"),
        }
    )


@bp.route("/schedule/run-now", methods=["POST"])
def run_now():
    body = request.get_json(silent=True) or {}
    config = get_config()
    ai_model = body.get("ai_model") or config.get("scheduler", {}).get("ai_model", "deepseek")
    VALID_MODELS = ("gpt-4o-mini", "gpt-4o", "gpt-4", "deepseek")
    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400
    result = trigger_run_now(ai_model)
    return jsonify(result), 202
