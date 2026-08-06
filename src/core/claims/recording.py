"""The write path: a complete resolution, or nothing.

Persistence is where the totality guarantee either survives or quietly stops
being one. Two things would end it here, and both are ordinary-looking code:

**Accepting rows instead of a resolution.** A recorder whose parameter is a
sequence of :class:`~src.core.contracts.provenance.ClaimSupport` can be handed
whatever an evaluator happened to return, and the completeness check
:class:`~src.core.claims.resolution.ClaimSupportResolution` performs is skipped
by simply not going through it. So the parameter is the resolution, and the
type is re-checked at runtime rather than left to the type checker alone —
static typing does not cover the call sites that matter most here, which are
the ones written later by someone who did not read this module.

**Committing part of a resolution.** This never commits. Every row is flushed
into the caller's transaction, as every repository in this codebase does, so
either all of a resolution's rows land or none of them do, and a crash midway
cannot leave an artifact whose claims are half accounted for.

``evidence_count`` is not passed at all: ``ClaimSupportRepository`` derives it
from ``evidence_ids`` at the one point a row is constructed. SQL cannot check
the denormalized count against the JSON array it summarizes, so the guarantee
is that no code path exists which could supply a different number.
"""

from typing import final

from src.core.claims.resolution import ClaimSupportResolution
from src.models.db.claim_support import AgentClaimSupport
from src.repositories.claim_support_repository import (
    ClaimSupportRepository,
    OrganizationId,
)

__all__ = ["ClaimSupportRecorder"]


@final
class ClaimSupportRecorder:
    """Persists every verdict of one resolution, in the caller's transaction."""

    __slots__ = ("_repository",)

    def __init__(self, repository: ClaimSupportRepository) -> None:
        self._repository = repository

    async def record(
        self,
        resolution: ClaimSupportResolution,
        *,
        organization_id: OrganizationId,
    ) -> tuple[AgentClaimSupport, ...]:
        """Append one row per material claim of ``resolution``.

        Args:
            resolution: A complete resolution. Its type is the guarantee that
                every material claim carries a verdict; nothing here re-derives
                that, and nothing else is accepted.
            organization_id: The authenticated tenant boundary, checked
                against the parent run by the repository.

        Returns:
            The persisted rows, in inventory order.

        Raises:
            TypeError: ``resolution`` is not a
                :class:`~src.core.claims.resolution.ClaimSupportResolution`.
            TenantMismatchError: The org context disagrees with the run's.
        """

        if not isinstance(resolution, ClaimSupportResolution):
            raise TypeError(
                "record() takes a ClaimSupportResolution, not "
                f"{type(resolution).__name__}; a bare collection of claim "
                "supports has not been checked against the material-claim "
                "inventory and may be missing claims"
            )

        rows = [
            await self._repository.record_claim_support(
                support, organization_id=organization_id
            )
            for support in resolution.supports
        ]
        return tuple(rows)
