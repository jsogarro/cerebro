"""Content sanitization for inter-agent prompt injection defense.

Implements conservative sanitization at MCP/external-source boundaries to neutralize
instruction-like patterns while preserving legitimate academic text. Logs all
neutralization events for security monitoring.

Based on multi-agent security hardening plan (Phase S3):
/Users/ogarro/work/VAULT/Plans/cerebro/2026-07-04-multiagent-security-hardening-plan.md
"""

import re
from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class NeutralizationReason(Enum):
    """Reason for content neutralization."""

    GOAL_HIJACKING = "goal_hijacking"  # "ignore previous", "new instructions"
    DELIMITER_ESCAPE = "delimiter_escape"  # XML/fence escape attempts
    EXCESSIVE_CAPS = "excessive_caps"  # Shouting patterns
    ENCODED_PAYLOAD = "encoded_payload"  # Base64, URL-encode
    SYSTEM_TAG = "system_tag"  # Fake system tags
    LONG_DELIMITER_SEQ = "long_delimiter_sequence"  # === / --- sequences


@dataclass
class SanitizationResult:
    """Result of content sanitization."""

    sanitized_text: str
    was_modified: bool
    neutralization_events: list[tuple[NeutralizationReason, str]]
    original_hash: str  # For forensic tracking


class ContentSanitizer:
    """Sanitizes external content to prevent prompt injection.

    Conservative approach: neutralize instruction-like patterns while preserving
    legitimate academic content. Designed to pass benign text UNCHANGED.

    Usage:
        sanitizer = ContentSanitizer()
        result = sanitizer.sanitize(untrusted_web_text)
        if result.was_modified:
            logger.info("Neutralized injection attempt", events=result.neutralization_events)
    """

    # Instruction hijacking patterns
    GOAL_HIJACKING_PATTERNS = [
        # Ignore previous/above/prior/all
        r"ignore\s+(previous|above|prior|all)(\s+\w+)?\s+(instructions?|prompts?|commands?)",
        r"disregard\s+(previous|above|prior|all)(\s+\w+)?\s+(instructions?|prompts?)",
        # New/override instructions
        r"(new|override|replace)\s+(system|instructions?|prompts?)",
        # Direct instruction override
        r"<\s*new[_-]?instructions?\s*>",
        r"system\s*:\s*(ignore|disregard|override)",
        # Goal redirection
        r"your\s+(new\s+)?(goal|task|objective)\s+is\s+(now\s+)?to",
        r"instead\s+of(?:(?!you).){0,120}you\s+(should|must|will)",
    ]

    # Delimiter escape patterns
    DELIMITER_ESCAPE_PATTERNS = [
        r"<\s*/?\s*system\s*>",  # <system> or </system>
        r"<\s*/?\s*assistant\s*>",  # <assistant> or </assistant>
        r"<\s*/?\s*user\s*>",  # <user> or </user>
        r"```\s*system",  # Code fence abuse
        r"```\s*ignore",  # Code fence with directives
        r"={5,}",  # Long === sequences
        r"-{5,}",  # Long --- sequences
        r"_{5,}",  # Long ___ sequences
    ]

    # Excessive caps pattern (50+ chars of mostly caps)
    EXCESSIVE_CAPS_PATTERN = re.compile(r"[A-Z\s]{50,}")

    # Encoded payloads
    ENCODED_PAYLOAD_PATTERNS = [
        r"data:text/plain;base64,",  # Base64 embedding
        r"(?:[A-Za-z0-9+/]{4}){20,}={0,2}",  # Raw base64 (80+ chars, 4-aligned)
        r"%[0-9A-Fa-f]{2}(%[0-9A-Fa-f]{2}){10,}",  # URL encoding chain
    ]

    def __init__(self, enable_logging: bool = True):
        """Initialize sanitizer.

        Args:
            enable_logging: Whether to log neutralization events

        """
        self.enable_logging = enable_logging
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for performance."""
        self._goal_hijacking_re = [
            re.compile(p, re.IGNORECASE) for p in self.GOAL_HIJACKING_PATTERNS
        ]
        self._delimiter_escape_re = [
            re.compile(p, re.IGNORECASE) for p in self.DELIMITER_ESCAPE_PATTERNS
        ]
        self._encoded_payload_re = [
            re.compile(p) for p in self.ENCODED_PAYLOAD_PATTERNS
        ]

    def sanitize(self, text: str) -> SanitizationResult:
        """Sanitize untrusted content.

        Args:
            text: Untrusted external content (e.g., paper title/abstract from arXiv)

        Returns:
            SanitizationResult with sanitized text and neutralization log

        """
        original_hash = str(hash(text))
        sanitized = text
        events: list[tuple[NeutralizationReason, str]] = []

        # 1. Neutralize goal hijacking patterns
        for pattern in self._goal_hijacking_re:
            matches = pattern.findall(sanitized)
            if matches:
                sanitized = pattern.sub("[INSTRUCTION_NEUTRALIZED]", sanitized)
                for match in matches:
                    match_str = match if isinstance(match, str) else str(match)
                    events.append((NeutralizationReason.GOAL_HIJACKING, match_str))

        # 2. Neutralize delimiter escapes
        for pattern in self._delimiter_escape_re:
            matches = pattern.findall(sanitized)
            if matches:
                sanitized = pattern.sub("[DELIMITER_NEUTRALIZED]", sanitized)
                for match in matches:
                    events.append((NeutralizationReason.DELIMITER_ESCAPE, match))

        # 3. Neutralize excessive caps
        caps_matches = self.EXCESSIVE_CAPS_PATTERN.findall(sanitized)
        if caps_matches:
            for match in caps_matches:
                sanitized = sanitized.replace(match, match.lower())
                events.append((NeutralizationReason.EXCESSIVE_CAPS, match[:50]))

        # 4. Neutralize encoded payloads
        for pattern in self._encoded_payload_re:
            matches = pattern.findall(sanitized)
            if matches:
                sanitized = pattern.sub("[ENCODED_PAYLOAD_REMOVED]", sanitized)
                for match in matches:
                    events.append(
                        (NeutralizationReason.ENCODED_PAYLOAD, match[:50] + "..."),
                    )

        # Build result
        was_modified = sanitized != text
        result = SanitizationResult(
            sanitized_text=sanitized,
            was_modified=was_modified,
            neutralization_events=events,
            original_hash=original_hash,
        )

        # Log if modified
        if was_modified and self.enable_logging:
            logger.info(
                "content_sanitization_neutralized_injection",
                original_hash=original_hash,
                neutralization_count=len(events),
                reasons=[r.value for r, _ in events],
            )

        return result

    def sanitize_batch(
        self,
        texts: list[str],
    ) -> list[SanitizationResult]:
        """Sanitize multiple texts.

        Args:
            texts: List of untrusted texts

        Returns:
            List of SanitizationResult objects

        """
        return [self.sanitize(text) for text in texts]
