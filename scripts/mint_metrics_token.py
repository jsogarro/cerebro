"""Print a ``/metrics`` scraper token signed with the configured JWT key.

Run where ``JWT_PRIVATE_KEY_PATH`` is readable, then store the output under the
remote key ``research-platform/metrics-token``. Usage: mint_metrics_token.py
"""

from src.auth.jwt_service import JWTService

if __name__ == "__main__":
    print(JWTService().generate_metrics_token())
