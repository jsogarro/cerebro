"""Print a ``/metrics`` scraper token signed with the configured JWT key.

Needs an existing ``JWT_PRIVATE_KEY_PATH``; it never generates or writes a key,
because a token signed by a throwaway key validates nowhere. Store the output
under the remote key ``research-platform/metrics-token``.
Usage: python scripts/mint_metrics_token.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.auth.jwt_service import JWTService
from src.core.config import settings


def main() -> int:
    key_path = settings.JWT_PRIVATE_KEY_PATH
    if not os.access(key_path, os.R_OK) or not os.path.isfile(key_path):
        print(f"JWT private key not readable at {key_path}", file=sys.stderr)
        return 1
    print(JWTService(private_key_path=key_path).generate_metrics_token())
    return 0


if __name__ == "__main__":
    sys.exit(main())
