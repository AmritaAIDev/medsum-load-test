#!/usr/bin/env python3
"""Launch MEDSUM Accuracy Testing Framework."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "medsum_test.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("medsum")
log.info("=== MedSum test server starting on port 5051 ===")

from medsum_testing.backend.app import app

if __name__ == "__main__":
    port = 5051
    print(f"\n  MEDSUM Accuracy Testing — http://127.0.0.1:{port}")
    print(f"  Health:  http://127.0.0.1:{port}/api/medsum-test/health")
    print(f"  Drive:   http://127.0.0.1:{port}/api/medsum-test/debug/drive\n")
    log.info("Listening on http://127.0.0.1:%s", port)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
