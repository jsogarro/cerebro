"""Tests for TalkHier provenance tagging (S1: prompt injection defense)."""

from src.agents.communication.talkhier_message import (
    MessageType,
    ProvenanceType,
    TalkHierContent,
    TalkHierMessage,
)


class TestProvenanceTagging:
    """Test suite for S1: Provenance tagging on TalkHier messages."""

    def test_talkhier_content_default_provenance(self):
        """TalkHierContent should default to LLM_GENERATED provenance."""
        content = TalkHierContent(content="Test content")

        assert content.source_type == ProvenanceType.LLM_GENERATED
        assert content.provenance_chain == []

    def test_talkhier_content_explicit_provenance(self):
        """TalkHierContent should accept explicit provenance type."""
        content = TalkHierContent(
            content="External data",
            source_type=ProvenanceType.EXTERNAL_WEB,
        )

        assert content.source_type == ProvenanceType.EXTERNAL_WEB

    def test_talkhier_content_provenance_chain(self):
        """TalkHierContent should track provenance chain."""
        content = TalkHierContent(
            content="Processed data",
            source_type=ProvenanceType.TOOL_OUTPUT,
            provenance_chain=["mcp_academic_search", "sanitizer"],
        )

        assert len(content.provenance_chain) == 2
        assert "mcp_academic_search" in content.provenance_chain

    def test_all_provenance_types_available(self):
        """All five provenance types should be available."""
        assert hasattr(ProvenanceType, "USER_INPUT")
        assert hasattr(ProvenanceType, "TOOL_OUTPUT")
        assert hasattr(ProvenanceType, "LLM_GENERATED")
        assert hasattr(ProvenanceType, "EXTERNAL_WEB")
        assert hasattr(ProvenanceType, "MEMORY_RETRIEVED")

    def test_provenance_enum_values(self):
        """Provenance enum values should be correct."""
        assert ProvenanceType.USER_INPUT.value == "user_input"
        assert ProvenanceType.TOOL_OUTPUT.value == "tool_output"
        assert ProvenanceType.LLM_GENERATED.value == "llm_generated"
        assert ProvenanceType.EXTERNAL_WEB.value == "external_web"
        assert ProvenanceType.MEMORY_RETRIEVED.value == "memory_retrieved"

    def test_talkhier_message_preserves_content_provenance(self):
        """TalkHierMessage should preserve content provenance."""
        content = TalkHierContent(
            content="Web scraped data",
            source_type=ProvenanceType.EXTERNAL_WEB,
        )

        message = TalkHierMessage(
            from_agent="literature_review",
            to_agent="supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=content,
        )

        assert message.talkhier_content.source_type == ProvenanceType.EXTERNAL_WEB

    def test_talkhier_message_string_content_gets_default_provenance(self):
        """TalkHierMessage with string content should get default provenance."""
        message = TalkHierMessage(
            from_agent="worker",
            to_agent="supervisor",
            message_type=MessageType.RESPONSE,
            content="Simple string content",
        )

        # String content is converted to TalkHierContent
        assert isinstance(message.talkhier_content, TalkHierContent)
        assert message.talkhier_content.source_type == ProvenanceType.LLM_GENERATED

    def test_talkhier_message_dict_content_accepts_provenance(self):
        """TalkHierMessage with dict content should accept source_type."""
        message = TalkHierMessage(
            from_agent="tool",
            to_agent="worker",
            message_type=MessageType.RESPONSE,
            content={
                "content": "Tool output data",
                "source_type": ProvenanceType.TOOL_OUTPUT,
            },
        )

        assert message.talkhier_content.source_type == ProvenanceType.TOOL_OUTPUT

    def test_legacy_message_conversion_preserves_structure(self):
        """Converting to legacy message should preserve all fields."""
        content = TalkHierContent(
            content="Test",
            source_type=ProvenanceType.USER_INPUT,
            provenance_chain=["user_interface"],
        )

        message = TalkHierMessage(
            from_agent="user",
            to_agent="supervisor",
            message_type=MessageType.REQUEST,
            content=content,
        )

        legacy = message.to_legacy_message()

        # Legacy message should have all the basic fields
        assert legacy.from_agent == "user"
        assert legacy.to_agent == "supervisor"
        assert "content" in legacy.content

    def test_provenance_field_is_optional_for_backward_compat(self):
        """Old code creating TalkHierContent without provenance should still work."""
        # This simulates old code that doesn't know about provenance
        content = TalkHierContent(
            content="Old style content",
            background="Some background",
        )

        # Should get default provenance
        assert content.source_type == ProvenanceType.LLM_GENERATED
        # Should have default empty chain
        assert content.provenance_chain == []


class TestProvenanceSerialization:
    """source_type/provenance_chain must survive to_dict/from_dict (R1 review)."""

    def test_source_type_survives_roundtrip(self):
        content = TalkHierContent(
            content="external abstract",
            source_type=ProvenanceType.EXTERNAL_WEB,
            provenance_chain=["mcp:arxiv", "sanitizer"],
        )
        msg = TalkHierMessage(
            from_agent="lit_review",
            to_agent="supervisor",
            message_type=MessageType.RESPONSE,
            content=content,
        )
        restored = TalkHierMessage.from_dict(msg.to_dict())
        assert restored.talkhier_content.source_type is ProvenanceType.EXTERNAL_WEB
        assert restored.talkhier_content.provenance_chain == ["mcp:arxiv", "sanitizer"]

    def test_default_provenance_survives_roundtrip(self):
        content = TalkHierContent(content="worker output")
        msg = TalkHierMessage(
            from_agent="worker",
            to_agent="supervisor",
            message_type=MessageType.RESPONSE,
            content=content,
        )
        restored = TalkHierMessage.from_dict(msg.to_dict())
        assert restored.talkhier_content.source_type is ProvenanceType.LLM_GENERATED


class TestProvenanceDeserializationSafety:
    """from_dict must not crash on tampered/unknown provenance (R2 review)."""

    def test_unknown_source_type_defaults_untrusted(self):
        content = TalkHierContent(content="x")
        msg = TalkHierMessage(
            from_agent="a",
            to_agent="b",
            message_type=MessageType.RESPONSE,
            content=content,
        )
        payload = msg.to_dict()
        payload["talkhier_content"]["source_type"] = "totally_bogus_value"
        restored = TalkHierMessage.from_dict(payload)
        # Fail safe: unknown provenance is treated as least-trusted external.
        assert restored.talkhier_content.source_type is ProvenanceType.EXTERNAL_WEB
