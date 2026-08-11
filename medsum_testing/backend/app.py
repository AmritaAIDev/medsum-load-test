"""MEDSUM Accuracy Testing Flask application."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from flask import Flask, jsonify, send_from_directory

from medsum_testing.backend.routes.report import bp as report_bp
from medsum_testing.backend.routes.results import bp as results_bp
from medsum_testing.backend.routes.scheduler import bp as scheduler_bp
from medsum_testing.backend.routes.test_runner import bp as test_runner_bp
from medsum_testing.backend.services.config_loader import get_config, get_repo_root
from medsum_testing.backend.services.scheduler_service import start_scheduler

FRONTEND_DIR = get_repo_root() / "medsum_testing" / "frontend"


def create_app() -> Flask:
    app = Flask(__name__)

    app.register_blueprint(test_runner_bp, url_prefix="/api/medsum-test")
    app.register_blueprint(results_bp, url_prefix="/api/medsum-test")
    app.register_blueprint(report_bp, url_prefix="/api/medsum-test")
    app.register_blueprint(scheduler_bp, url_prefix="/api/medsum-test")

    @app.route("/api/medsum-test/health")
    def health():
        port = int(os.environ.get("MEDSUM_TEST_PORT", 5051))
        return jsonify({"status": "ok", "port": port})

    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "medsum_test.html")

    @app.route("/css/<path:filename>")
    def css(filename: str):
        return send_from_directory(FRONTEND_DIR / "css", filename)

    @app.route("/js/<path:filename>")
    def js(filename: str):
        return send_from_directory(FRONTEND_DIR / "js", filename)

    with app.app_context():
        try:
            cfg = get_config()
            start_scheduler(cfg)
        except Exception as exc:
            app.logger.warning("Startup warning (non-fatal): %s", exc)

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("MEDSUM_TEST_PORT", 5051))
    print(f"\n  MEDSUM Accuracy Testing Framework")
    print(f"  Open: http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
