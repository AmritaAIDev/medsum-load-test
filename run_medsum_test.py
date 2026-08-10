#!/usr/bin/env python3
"""Launch MEDSUM Accuracy Testing Framework."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from medsum_testing.backend.app import app

if __name__ == "__main__":
    port = int(os.environ.get("MEDSUM_TEST_PORT", 5051))
    print(f"\n  MEDSUM Accuracy Testing Framework")
    print(f"  Open: http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
