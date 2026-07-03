#!/usr/bin/env python3
"""CLI entry point for adaptive routing offline evaluation."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.ai_brain.experimentation.eval.adaptive_routing_eval import main

if __name__ == "__main__":
    asyncio.run(main())
