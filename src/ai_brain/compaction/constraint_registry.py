"""Constraint Registry for Context Compaction

Extracts and re-injects critical constraints to mitigate governance-decay during
context compaction. Implements provenance tracking for integration with security
hardening features.

Lifecycle (PR2 + PR3):
  PR2 (this module): extract() captures constraints from the user query per
    request; inject() is wired and functional but has no production callsite yet.
  PR3: inject() will be called at the compaction boundary to prepend the
    [PINNED CONSTRAINTS] block before handing the compacted context to the model.
  The registry is intentionally request-local — it MUST NOT be stored on the
  supervisor instance (supervisors are shared across concurrent requests; storing
  a registry on self would cause cross-request constraint leakage).

Key Features:
- Pattern-based constraint extraction (routing, QA, format, security)
- Precompiled patterns for extraction performance
- Prompt-injection hardening on inject() (PR #72 defense chain)
- Deduplication by (type, content) across multiple extract() calls
- Re-extraction immunity: inject() output is stripped before pattern matching
- Provenance tracking (future integration with feat/security-hardening-s)
- Dark launch via ENABLE_CONSTRAINT_PINNING flag (extraction-only until PR3)
"""

import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar

from structlog import get_logger

from src.security.content_sanitizer import ContentSanitizer

logger = get_logger()

# Precompiled block delimiters used by inject() — shared across instances.
_BLOCK_START = "[PINNED CONSTRAINTS]"
_BLOCK_END = "[END CONSTRAINTS]"
_EXISTING_BLOCK_RE = re.compile(
    r"\[PINNED CONSTRAINTS\].*?\[END CONSTRAINTS\]\n?\n?",
    re.DOTALL | re.IGNORECASE,
)

# Guard against processing pathologically large contexts (64 KB).
_MAX_CONTEXT_BYTES = 64 * 1024
# Maximum constraints extracted per type per extract() call.
_MAX_PER_TYPE = 10
# Maximum length of any single extracted constraint value (characters).
_MAX_VALUE_LEN = 200
# Sentence terminators: stop capture at first occurrence.
# '.' only matches as a sentence boundary (followed by whitespace or end-of-string)
# so that numeric values like "0.9" are not truncated at the decimal point.
_VALUE_TERMINATOR_RE = re.compile(r"(?:\.(?=\s|$)|;|\n)")

# Module-level ContentSanitizer instance (stateless; safe to share).
_sanitizer = ContentSanitizer(enable_logging=True)


class ConstraintType(Enum):
    """Types of constraints that can be extracted."""

    ROUTING = "routing"
    QA = "qa"
    FORMAT = "format"
    SECURITY = "security"


@dataclass
class ExtractedConstraint:
    """A single extracted constraint with metadata."""

    constraint_type: ConstraintType
    content: str
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_type: str | None = None  # Provenance: USER_INPUT | TOOL_OUTPUT | etc.
    confidence: float = 1.0  # reserved; always 1.0


@dataclass
class ConstraintExtractionResult:
    """Result of constraint extraction operation."""

    constraints: list[ExtractedConstraint]
    extraction_time_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_extracted(self) -> int:
        """Number of constraints extracted in this call."""
        return len(self.constraints)


class ConstraintRegistry:
    """Registry for extracting and re-injecting critical constraints.

    This registry identifies and preserves critical constraints during context
    compaction to prevent governance-decay (loss of routing strategy, QA criteria,
    format requirements, security rules).

    Attributes:
        enabled: Feature flag for constraint pinning (default False)
        constraint_types: List of constraint types to extract
        _compiled_patterns: Precompiled regex patterns, keyed by ConstraintType

    Design notes:
    - MUST be instantiated per-request; see module docstring for why.
    - inject() is injection-hardened (PR #72 defense chain) but has no callsite
      in PR2; it will be wired at the compaction boundary in PR3.
    - injection_wired=False in the constraints_injected log event indicates
      measurement-only mode until PR3 lands.
    """

    # Regex patterns for constraint extraction.
    # Each pattern MUST define exactly one capture group — the constraint value.
    # ClassVar: read-only at runtime; do not mutate after class definition.
    PATTERNS: ClassVar[dict[ConstraintType, list[str]]] = {
        ConstraintType.ROUTING: [
            r"routing\s+strategy:\s*([^\n]+)",
            r"route\s+to:\s*([^\n]+)",
            r"routing\s+mode:\s*([^\n]+)",
            r"use\s+([a-z_]+)\s+routing",
        ],
        ConstraintType.QA: [
            r"quality\s+threshold:\s*([^\n]+)",
            r"verification\s+required:\s*([^\n]+)",
            r"must\s+verify:\s*([^\n]+)",
            r"qa\s+criteria:\s*([^\n]+)",
        ],
        ConstraintType.FORMAT: [
            r"format:\s*([^\n]+)",
            r"output\s+format:\s*([^\n]+)",
            r"must\s+be\s+formatted\s+as:\s*([^\n]+)",
            r"structured\s+as:\s*([^\n]+)",
        ],
        ConstraintType.SECURITY: [
            r"security\s+requirement:\s*([^\n]+)",
            r"must\s+sanitize:\s*([^\n]+)",
            r"authentication\s+required:\s*([^\n]+)",
            r"access\s+control:\s*([^\n]+)",
        ],
    }

    def __init__(
        self,
        enabled: bool = False,
        constraint_types: list[str] | None = None,
    ):
        """Initialize the constraint registry.

        Args:
            enabled: Whether constraint pinning is enabled (dark launch flag)
            constraint_types: List of constraint type strings to track.
                Unrecognised values are skipped with a warning; if none are
                valid (or the list is empty), all types are used as the fallback.
        """
        self.enabled = enabled

        if constraint_types is not None:
            valid: list[ConstraintType] = []
            valid_values = {ct.value for ct in ConstraintType}
            for ct_str in constraint_types:
                if ct_str in valid_values:
                    valid.append(ConstraintType(ct_str))
                else:
                    logger.warning(
                        "constraint_registry_unknown_type_skipped",
                        constraint_type=ct_str,
                        valid_types=list(valid_values),
                    )
            if valid:
                self.constraint_types = valid
            else:
                # No valid types remain (all were invalid or the list was empty).
                # Fall back to all types rather than silently doing nothing.
                self.constraint_types = list(ConstraintType)
                logger.warning(
                    "constraint_registry_empty_types_fallback",
                    reason="no_valid_constraint_types_provided",
                    fallback_types=[ct.value for ct in ConstraintType],
                )
        else:
            self.constraint_types = list(ConstraintType)

        # Precompile patterns once — dict[ConstraintType, list[re.Pattern]]
        self._compiled_patterns: dict[ConstraintType, list[re.Pattern[str]]] = {
            ct: [re.compile(p, re.IGNORECASE) for p in patterns]
            for ct, patterns in self.PATTERNS.items()
        }

        # Storage for extracted constraints (request-local — not shared).
        self._constraints: list[ExtractedConstraint] = []

        logger.debug(
            "constraint_registry_initialized",
            enabled=self.enabled,
            constraint_types=[ct.value for ct in self.constraint_types],
        )

    def extract(
        self, context: str | None, source_type: str | None = None
    ) -> ConstraintExtractionResult:
        """Extract constraints from context string.

        Constraints are deduplicated by (constraint_type, content) across calls.
        Any existing [PINNED CONSTRAINTS]...[END CONSTRAINTS] block is stripped
        before pattern matching, making extract(inject(x)) a no-op.

        Extraction is skipped (empty result, warning logged) if:
        - context is None or not a string
        - context exceeds 64 KB

        Args:
            context: The context string to extract constraints from
            source_type: Provenance type (USER_INPUT, TOOL_OUTPUT, etc.)

        Returns:
            ConstraintExtractionResult with newly extracted constraints and metadata
        """
        if not self.enabled:
            logger.debug("constraint_extraction_skipped", reason="feature_disabled")
            return ConstraintExtractionResult(
                constraints=[],
                extraction_time_ms=0.0,
                metadata={"enabled": False},
            )

        if not isinstance(context, str):
            logger.warning(
                "constraint_extraction_skipped",
                reason="invalid_context_type",
                context_type=type(context).__name__,
            )
            return ConstraintExtractionResult(
                constraints=[],
                extraction_time_ms=0.0,
                metadata={"enabled": True, "skipped": "invalid_type"},
            )

        encoded = context.encode()
        if len(encoded) > _MAX_CONTEXT_BYTES:
            logger.warning(
                "constraint_extraction_skipped",
                reason="context_too_large",
                context_bytes=len(encoded),
                limit_bytes=_MAX_CONTEXT_BYTES,
            )
            return ConstraintExtractionResult(
                constraints=[],
                extraction_time_ms=0.0,
                metadata={"enabled": True, "skipped": "context_too_large"},
            )

        start_time = time.monotonic()

        # Strip any existing injected block before pattern matching so that
        # extract(inject(x)) does not re-extract already-pinned constraints.
        search_text = _EXISTING_BLOCK_RE.sub("", context)

        # Build dedup key set from already-stored constraints.
        # Keys are normalised to lowercase so case-variants ("Quality_Focused" vs
        # "quality_focused") collapse to a single entry; original-case content is
        # preserved in the stored ExtractedConstraint.
        existing_keys: set[tuple[ConstraintType, str]] = {
            (c.constraint_type, c.content.strip().lower()) for c in self._constraints
        }

        newly_extracted: list[ExtractedConstraint] = []

        for constraint_type in self.constraint_types:
            # Count already-stored constraints of this type so the cumulative
            # total across all extract() calls never exceeds _MAX_PER_TYPE.
            already_stored = sum(
                1 for c in self._constraints if c.constraint_type == constraint_type
            )
            per_type_count = already_stored

            for compiled_pattern in self._compiled_patterns.get(constraint_type, []):
                for match in compiled_pattern.finditer(search_text):
                    if per_type_count >= _MAX_PER_TYPE:
                        logger.warning(
                            "constraint_extraction_per_type_limit_reached",
                            constraint_type=constraint_type.value,
                            limit=_MAX_PER_TYPE,
                        )
                        break

                    # Use capture group(1) — all patterns define one group.
                    # Fall back to group(0) only if group(1) is None (shouldn't
                    # happen with current patterns, but is defensive).
                    raw_content = match.group(1)
                    if raw_content is None:
                        raw_content = match.group(0)
                    content = raw_content.strip()

                    # FIX-3: Bound captured value at the first sentence terminator
                    # (. ; or newline) to prevent whole-sentence tail over-capture,
                    # then hard-cap at _MAX_VALUE_LEN characters.
                    term_match = _VALUE_TERMINATOR_RE.search(content)
                    if term_match:
                        content = content[: term_match.start()].strip()
                    content = content[:_MAX_VALUE_LEN]

                    # Normalise key to lowercase for case-insensitive dedup;
                    # the original-case content is stored in the constraint.
                    dedup_key = (constraint_type, content.lower())
                    if dedup_key in existing_keys:
                        continue
                    existing_keys.add(dedup_key)

                    extracted_constraint = ExtractedConstraint(
                        constraint_type=constraint_type,
                        content=content,
                        source_type=source_type,
                    )
                    newly_extracted.append(extracted_constraint)
                    self._constraints.append(extracted_constraint)
                    per_type_count += 1

                    # FIX-2: Never log raw content — log type, length, and hash only.
                    _content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
                    logger.debug(
                        "constraint_extracted",
                        constraint_type=constraint_type.value,
                        content_len=len(content),
                        content_hash=_content_hash,
                        source_type=source_type,
                    )

        extraction_time_ms = (time.monotonic() - start_time) * 1000

        result = ConstraintExtractionResult(
            constraints=newly_extracted,
            extraction_time_ms=extraction_time_ms,
            metadata={
                "enabled": True,
                "constraint_types": [ct.value for ct in self.constraint_types],
            },
        )

        logger.info(
            "constraint_extraction_complete",
            total_extracted=result.total_extracted,
            extraction_time_ms=result.extraction_time_ms,
            constraint_types={
                ct.value: sum(1 for c in newly_extracted if c.constraint_type == ct)
                for ct in ConstraintType
            },
        )

        return result

    def inject(self, context: str) -> str:
        """Inject pinned constraints into context string.

        Adds a [PINNED CONSTRAINTS] block at the beginning of the context with
        all extracted constraints that should be preserved.

        SECURITY: constraint content originates from raw user queries and may
        contain adversarial payloads. This method applies a three-layer defense
        (PR #72 defense chain) before interpolating content into the block:
          1. Embedded newlines are stripped (prevents block structure breakout).
          2. Literal block delimiter strings are neutralised (prevents an attacker
             from injecting a fake [END CONSTRAINTS] to close the block early and
             escape into the main prompt).
          3. Content is routed through ContentSanitizer.sanitize() to catch
             goal-hijacking, delimiter-escape, and encoded-payload patterns.

        NOTE — PR2 stub: this method has no production callsite yet. Wiring
        at the compaction boundary lands in PR3. The constraints_injected log
        event carries injection_wired=False to make this explicit.

        Args:
            context: The context string to inject constraints into

        Returns:
            Modified context with pinned constraints block prepended
        """
        if not self.enabled or not self._constraints:
            return context

        # Build constraint block with sanitized content.
        constraint_lines = [_BLOCK_START]
        for constraint in self._constraints:
            # Layer 1: strip embedded newlines.
            safe_content = constraint.content.replace("\n", " ").replace("\r", " ")
            # NFKC normalisation folds unicode look-alike brackets (U+FF3B/U+FF3D
            # fullwidth) to their ASCII equivalents before delimiter matching so
            # that variants like U+FF3B..U+FF3D (fullwidth brackets) are caught by Layer 2.
            safe_content = unicodedata.normalize("NFKC", safe_content)
            # Layer 2: neutralise any literal block delimiter strings (case-
            # insensitive so that lowercase/mixed variants also cannot escape the
            # block early).
            safe_content = re.sub(
                re.escape(_BLOCK_START),
                "[PINNED_CONSTRAINTS]",
                safe_content,
                flags=re.IGNORECASE,
            )
            safe_content = re.sub(
                re.escape(_BLOCK_END),
                "[END_CONSTRAINTS]",
                safe_content,
                flags=re.IGNORECASE,
            )
            # Layer 3: route through PR #72 ContentSanitizer.
            sanitization_result = _sanitizer.sanitize(safe_content)
            safe_content = sanitization_result.sanitized_text

            constraint_lines.append(f"- {safe_content}")
        constraint_lines.append(_BLOCK_END)

        constraint_block = "\n".join(constraint_lines)

        # FIX-4: Neutralize any pre-existing [PINNED CONSTRAINTS]...[END CONSTRAINTS]
        # delimiters already present in `context` before prepending the trusted block.
        # Apply NFKC first so fullwidth look-alike brackets fold to ASCII, then
        # replace the delimiter strings (case-insensitive) so only the real trusted
        # block is authoritative when PR3 wires inject().
        safe_context = unicodedata.normalize("NFKC", context)
        safe_context = re.sub(
            re.escape(_BLOCK_START),
            "[PINNED_CONSTRAINTS]",
            safe_context,
            flags=re.IGNORECASE,
        )
        safe_context = re.sub(
            re.escape(_BLOCK_END),
            "[END_CONSTRAINTS]",
            safe_context,
            flags=re.IGNORECASE,
        )

        # Inject at the beginning of the sanitized context.
        injected_context = f"{constraint_block}\n\n{safe_context}"

        logger.info(
            "constraints_injected",
            total_constraints=len(self._constraints),
            constraint_block_size=len(constraint_block),
            injection_wired=False,  # PR3 will set this to True at the callsite
        )

        return injected_context

    def clear(self) -> None:
        """Clear all stored constraints."""
        self._constraints.clear()
        logger.debug("constraints_cleared")

    def get_constraints(
        self,
        constraint_type: ConstraintType | None = None,
    ) -> list[ExtractedConstraint]:
        """Get stored constraints, optionally filtered by type.

        Args:
            constraint_type: Filter by constraint type (None = all)

        Returns:
            List of extracted constraints
        """
        if constraint_type is None:
            return self._constraints.copy()
        return [c for c in self._constraints if c.constraint_type == constraint_type]

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        return {
            "enabled": self.enabled,
            "total_constraints": len(self._constraints),
            "by_type": {
                ct.value: len(self.get_constraints(ct)) for ct in ConstraintType
            },
            "constraint_types_tracked": [ct.value for ct in self.constraint_types],
        }
