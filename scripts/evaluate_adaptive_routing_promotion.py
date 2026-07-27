#!/usr/bin/env python3
"""Run the manual, non-activating adaptive-routing promotion gate."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ai_brain.experimentation.eval.adaptive_routing_promotion import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
