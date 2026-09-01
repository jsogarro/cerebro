"""The tool-execution boundary: one mediated path for every tool call."""

from .audit import (
    EVENT_COMPLETED,
    EVENT_REQUESTED,
    EVENT_TYPE_VERSION,
    TOOL_INVOCATION_AGGREGATE,
    NullEventPublisher,
    PendingToolAuditStore,
    ToolAuditEvent,
    ToolAuditStore,
    ToolEventPublisher,
)
from .boundary import CancellationToken, Clock, IdFactory, ToolBoundary
from .circuit import BreakerRegistry, BreakerState, CircuitBreaker
from .errors import (
    CapabilityDecisionUnusableError,
    IdempotencyConflictError,
    PromptBindingRefusedError,
    ToolBoundaryError,
    ToolInvocationConflictError,
    ToolNotRegisteredError,
    ToolOutcomeNotSuccessfulError,
    ToolSpecError,
    UnknownSecretError,
)
from .outcome import RetryDisposition, ToolOutcome, ToolOutcomeStatus
from .prompts import (
    PromptIdentityVerifier,
    PromptRegistry,
    PromptRenderer,
    PromptTemplate,
    RenderedPrompt,
    require_rendered_prompt,
)
from .secrets import (
    MappingSecretProvider,
    NullSecretProvider,
    SecretProvider,
    validate_secret_provider,
)
from .spec import ToolCallContext, ToolHandler, ToolSpec

__all__ = [
    "EVENT_COMPLETED",
    "EVENT_REQUESTED",
    "EVENT_TYPE_VERSION",
    "TOOL_INVOCATION_AGGREGATE",
    "BreakerRegistry",
    "BreakerState",
    "CancellationToken",
    "CapabilityDecisionUnusableError",
    "CircuitBreaker",
    "Clock",
    "IdFactory",
    "IdempotencyConflictError",
    "MappingSecretProvider",
    "NullEventPublisher",
    "NullSecretProvider",
    "PendingToolAuditStore",
    "PromptBindingRefusedError",
    "PromptIdentityVerifier",
    "PromptRegistry",
    "PromptRenderer",
    "PromptTemplate",
    "RenderedPrompt",
    "RetryDisposition",
    "SecretProvider",
    "ToolAuditEvent",
    "ToolAuditStore",
    "ToolBoundary",
    "ToolBoundaryError",
    "ToolCallContext",
    "ToolEventPublisher",
    "ToolHandler",
    "ToolInvocationConflictError",
    "ToolNotRegisteredError",
    "ToolOutcome",
    "ToolOutcomeNotSuccessfulError",
    "ToolOutcomeStatus",
    "ToolSpec",
    "ToolSpecError",
    "UnknownSecretError",
    "require_rendered_prompt",
    "validate_secret_provider",
]
