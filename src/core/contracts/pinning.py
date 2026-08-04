"""Version pins captured when a run is admitted.

A run's observable semantics depend on more than its workflow definition and
routing policy: prompts, evaluators, artifact schemas, and policies all evolve
independently. Persisting the exact versions a run was admitted under lets a
run that outlives a deployment be replayed, resumed, and audited against the
components it actually used rather than whatever is current.
"""

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from .base import ContractId, ContractModel, Version


class PinnedComponentKind(StrEnum):
    """The component families whose versions a run pins individually."""

    PROMPT = "prompt"
    EVALUATOR = "evaluator"
    ARTIFACT_SCHEMA = "artifact_schema"
    POLICY = "policy"
    TOOL = "tool"


class PinnedComponentVersion(ContractModel):
    """One component identity pinned to the version a run was admitted with."""

    kind: PinnedComponentKind
    component_id: ContractId
    version: Version


class PinnedVersions(ContractModel):
    """The complete version pin recorded on a run's configuration snapshot.

    ``workflow_definition`` and ``routing_policy`` are pinned on the run record
    itself as well; they are repeated here so a snapshot is self-describing and
    can be validated without joining back to the run.
    """

    workflow_definition_id: ContractId
    workflow_definition_version: Version
    routing_policy_id: ContractId
    routing_policy_version: Version
    event_envelope_version: Version
    components: tuple[PinnedComponentVersion, ...] = ()

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        identities = [
            (component.kind, component.component_id) for component in self.components
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("A component may be pinned to only one version")
        return self

    def version_of(
        self, kind: PinnedComponentKind, component_id: str
    ) -> Version | None:
        """Return the pinned version for one component, or None if unpinned."""

        for component in self.components:
            if component.kind is kind and component.component_id == component_id:
                return component.version
        return None

    def versions_for(self, kind: PinnedComponentKind) -> dict[str, Version]:
        """Return every pinned ``component_id -> version`` for one kind."""

        return {
            component.component_id: component.version
            for component in self.components
            if component.kind is kind
        }
