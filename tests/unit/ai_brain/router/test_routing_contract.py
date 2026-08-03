"""Compatibility checks for the canonical routing contract seam."""

from src.ai_brain.router.routing_types import CollaborationMode as RouterMode
from src.core.contracts import CollaborationMode


def test_router_reexports_the_canonical_collaboration_mode() -> None:
    assert RouterMode is CollaborationMode
    assert tuple(mode.value for mode in RouterMode) == (
        "fast_path",
        "direct",
        "parallel",
        "hierarchical",
        "debate",
        "ensemble",
    )
