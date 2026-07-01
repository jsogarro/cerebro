"""Regression test for consensus_score=0.0 bug fix.

Tests that consensus evaluation produces non-degenerate scores when worker
results lack explicit evidence lists, preventing the observed production bug
where consensus_score=0.0 and evidence_quality=0.0 despite valid worker outputs.
"""

import pytest

from src.agents.communication.consensus_builder import ConsensusBuilder
from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)


@pytest.mark.asyncio
async def test_consensus_without_evidence_lists():
    """
    Test that consensus scoring works when workers don't provide evidence lists.

    This reproduces the production bug where research workers returned valid
    TalkHierContent without populating the evidence field, causing:
    - consensus_score=0.0
    - evidence_quality=0.0

    After fix, should produce non-degenerate scores based on confidence.
    """
    builder = ConsensusBuilder()

    # Simulate realistic worker results without evidence field populated
    workers = [
        ("literature_review", "Literature review of AI ethics papers", 0.85),
        ("methodology", "Research methodology design for qualitative study", 0.80),
        (
            "synthesis",
            "Synthesis of findings across multiple theoretical frameworks",
            0.90,
        ),
        ("comparative_analysis", "Comparative analysis of three approaches", 0.82),
        ("citation", "Citation verification and formatting", 0.88),
    ]

    messages = []
    for worker_type, content_text, confidence in workers:
        content = TalkHierContent(
            content=content_text,
            background=f"Background context for {worker_type}",
            intermediate_outputs={"worker_type": worker_type, "completed": True},
            confidence_score=confidence,
            # evidence field defaults to [] - this was the bug trigger
        )
        msg = TalkHierMessage(
            from_agent=worker_type,
            to_agent="research_supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=content,
            conversation_id="test-research-001",
        )
        messages.append(msg)

    consensus = await builder.evaluate_consensus(messages)

    # After fix, scores should be non-zero even without evidence
    assert consensus.overall_score > 0.0, (
        f"overall_score should be non-zero when workers provide valid content "
        f"without evidence lists, got {consensus.overall_score}"
    )

    assert consensus.evidence_quality > 0.0, (
        f"evidence_quality should use confidence as baseline when evidence "
        f"is absent, got {consensus.evidence_quality}"
    )

    # Verify it's using confidence scores reasonably
    # With confidence ~0.85, overall_score should be near that value
    # (EVIDENCE_BASED method falls back to SIMPLE_AVERAGE when no evidence)
    expected_avg = sum(c for _, _, c in workers) / len(workers)
    assert abs(consensus.overall_score - expected_avg) < 0.05, (
        f"overall_score {consensus.overall_score} should be near confidence "
        f"average {expected_avg} when using fallback"
    )

    # Evidence quality should be ~0.5 * confidence when no evidence provided
    expected_evidence_quality = expected_avg * 0.5
    assert abs(consensus.evidence_quality - expected_evidence_quality) < 0.05, (
        f"evidence_quality {consensus.evidence_quality} should be ~0.5 * "
        f"confidence avg when no evidence, expected ~{expected_evidence_quality}"
    )


@pytest.mark.asyncio
async def test_consensus_with_mixed_evidence():
    """Test consensus when some workers provide evidence and others don't."""
    builder = ConsensusBuilder()

    messages = [
        TalkHierMessage(
            from_agent="worker_with_evidence",
            to_agent="supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=TalkHierContent(
                content="Analysis with citations",
                confidence_score=0.9,
                evidence=["Source 1", "Source 2", "Source 3"],
            ),
            conversation_id="test-002",
        ),
        TalkHierMessage(
            from_agent="worker_without_evidence",
            to_agent="supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=TalkHierContent(
                content="Analysis without explicit evidence",
                confidence_score=0.8,
                # evidence defaults to []
            ),
            conversation_id="test-002",
        ),
    ]

    consensus = await builder.evaluate_consensus(messages)

    # Should produce reasonable scores when evidence is mixed
    assert consensus.overall_score > 0.0
    assert consensus.evidence_quality > 0.0

    # Evidence quality should be between 0 (no evidence) and 1.0 (full evidence)
    assert 0.0 < consensus.evidence_quality < 1.0


@pytest.mark.asyncio
async def test_consensus_with_full_evidence():
    """Test consensus when all workers provide evidence (ideal case)."""
    builder = ConsensusBuilder()

    messages = [
        TalkHierMessage(
            from_agent=f"worker_{i}",
            to_agent="supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=TalkHierContent(
                content=f"Analysis {i}",
                confidence_score=0.85,
                evidence=[f"Source {i}-{j}" for j in range(5)],
            ),
            conversation_id="test-003",
        )
        for i in range(4)
    ]

    consensus = await builder.evaluate_consensus(messages)

    # With full evidence from all workers, quality should be high
    assert consensus.overall_score > 0.8
    assert consensus.evidence_quality >= 0.8
