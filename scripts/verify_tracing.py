"""Live verification script for Langfuse tracing.

Run with LANGFUSE_ENABLED=true and keys set to verify traces are created.
Run with LANGFUSE_ENABLED=false to verify zero overhead.

Usage:
    # With tracing enabled (requires Langfuse keys)
    export LANGFUSE_ENABLED=true
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    python scripts/verify_tracing.py

    # With tracing disabled (no keys needed)
    export LANGFUSE_ENABLED=false
    python scripts/verify_tracing.py
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider
from src.ai_brain.providers.base_provider import ModelRequest


async def verify_tracing_enabled() -> None:
    """Verify tracing with LANGFUSE_ENABLED=true."""
    print("\n=== Verifying Tracing ENABLED ===\n")

    # Check environment
    enabled = os.getenv("LANGFUSE_ENABLED", "").lower() in ("1", "true", "yes")
    if not enabled:
        print("ERROR: LANGFUSE_ENABLED must be set to 'true'")
        sys.exit(1)

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        print("WARNING: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY not set")
        print("Traces will fail to initialize but code should not crash")

    # Create MASR router
    print("1. Creating MASR router...")
    router = MASRouter()

    # Route a test query
    print("2. Routing test query...")
    query = "What is artificial intelligence?"

    try:
        decision = await router.route(query)
        print(f"✓ Routing succeeded: query_id={decision.query_id}")
        print(f"  - Complexity: {decision.complexity_analysis.complexity_level.value}")
        print(f"  - Strategy: {decision.routing_strategy.value}")
        print(f"  - Estimated cost: ${decision.estimated_cost:.4f}")

        # Check if trace was created (indirect - we can't access the trace object)
        print("\n3. Checking trace creation...")
        from src.core.tracing import get_langfuse_client
        client = get_langfuse_client()

        if client is not None:
            print(f"✓ Langfuse client initialized (host: {getattr(client, 'host', 'unknown')})")
            print("  Note: Actual trace visibility requires checking Langfuse UI")
        else:
            print("✗ Langfuse client is None (check keys and SDK installation)")

    except Exception as e:
        print(f"✗ Routing failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def verify_tracing_disabled() -> None:
    """Verify zero overhead with LANGFUSE_ENABLED=false."""
    print("\n=== Verifying Tracing DISABLED ===\n")

    # Check environment
    enabled = os.getenv("LANGFUSE_ENABLED", "").lower() in ("1", "true", "yes")
    if enabled:
        print("ERROR: LANGFUSE_ENABLED must be set to 'false' or unset")
        sys.exit(1)

    print("1. Creating MASR router...")
    router = MASRouter()

    print("2. Routing test query...")
    query = "What is machine learning?"

    try:
        decision = await router.route(query)
        print(f"✓ Routing succeeded: query_id={decision.query_id}")

        # Verify no client was created
        print("\n3. Verifying zero tracing overhead...")
        from src.core.tracing import get_langfuse_client
        client = get_langfuse_client()

        if client is None:
            print("✓ Langfuse client is None (no overhead)")
        else:
            print("✗ Langfuse client was created despite flag being off!")
            sys.exit(1)

    except Exception as e:
        print(f"✗ Routing failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def main() -> None:
    """Run verification based on LANGFUSE_ENABLED setting."""
    enabled = os.getenv("LANGFUSE_ENABLED", "").lower() in ("1", "true", "yes")

    if enabled:
        await verify_tracing_enabled()
    else:
        await verify_tracing_disabled()

    print("\n=== Verification PASSED ===\n")


if __name__ == "__main__":
    asyncio.run(main())
