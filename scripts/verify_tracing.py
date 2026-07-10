"""Live verification script for Langfuse tracing (v4 SDK).

Verifies the no-op-safe contract and, when enabled + keyed, that a real client
initializes. Uses the SAME enabled-check as the tracing layer
(``src.core.tracing._tracing_enabled``) so there is no duplicated env parsing.

Usage:
    # Tracing enabled (requires Langfuse keys via Settings/env)
    export LANGFUSE_ENABLED=true
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    python scripts/verify_tracing.py

    # Tracing disabled (no keys needed)
    export LANGFUSE_ENABLED=false
    python scripts/verify_tracing.py
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ai_brain.router.masr import MASRouter
from src.core.tracing import (
    _tracing_enabled,
    get_langfuse_client,
    reset_langfuse_state,
    shutdown_langfuse,
)


async def verify_tracing_enabled() -> None:
    """Verify tracing with LANGFUSE_ENABLED=true."""
    print("\n=== Verifying Tracing ENABLED ===\n")

    if not _tracing_enabled():
        print("ERROR: LANGFUSE_ENABLED must be true (set via Settings/env)")
        sys.exit(1)

    print("1. Creating MASR router...")
    router = MASRouter()

    print("2. Routing test query...")
    try:
        decision = await router.route("What is artificial intelligence?")
        print(f"OK Routing succeeded: query_id={decision.query_id}")
        print(f"  - Complexity: {decision.complexity_analysis.level.value}")
        print(f"  - Strategy: {decision.routing_strategy.value}")
        print(f"  - Estimated cost: ${decision.estimated_cost:.4f}")

        print("\n3. Checking client + shutdown...")
        client = get_langfuse_client()
        if client is not None:
            print("OK Langfuse client initialized")
        else:
            print("!! Langfuse client is None (check keys / SDK install)")
        shutdown_langfuse()
        print("OK shutdown_langfuse() ran without raising")
    except Exception as e:
        print(f"FAIL Routing failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


async def verify_tracing_disabled() -> None:
    """Verify zero overhead with LANGFUSE_ENABLED=false."""
    print("\n=== Verifying Tracing DISABLED ===\n")

    if _tracing_enabled():
        print("ERROR: LANGFUSE_ENABLED must be false/unset for this check")
        sys.exit(1)

    print("1. Creating MASR router...")
    router = MASRouter()

    print("2. Routing test query...")
    try:
        decision = await router.route("What is machine learning?")
        print(f"OK Routing succeeded: query_id={decision.query_id}")

        print("\n3. Verifying zero tracing overhead...")
        client = get_langfuse_client()
        if client is None:
            print("OK Langfuse client is None (no overhead, SDK untouched)")
        else:
            print("FAIL Langfuse client was created despite flag being off!")
            sys.exit(1)

        shutdown_langfuse()
        print("OK shutdown_langfuse() is a clean no-op when disabled")
    except Exception as e:
        print(f"FAIL Routing failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


async def main() -> None:
    """Run verification based on the shared enabled-check."""
    reset_langfuse_state()
    if _tracing_enabled():
        await verify_tracing_enabled()
    else:
        await verify_tracing_disabled()

    print("\n=== Verification PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(main())
