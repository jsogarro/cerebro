"""The four-field brief a plan hands each worker.

Only the plan runtime builds a brief, from its own ``WorkerAssignment``; the
worker honors it only as a ``DelegationContract`` instance in task context. A
brief read back out of client-controllable dicts would let a request rewrite
what the worker is told to do, so none is.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from src.security.content_sanitizer import escape_for_delimited_prompt

if TYPE_CHECKING:
    from src.core.contracts import WorkerAssignment

DELEGATION_BRIEF_CONTEXT_KEY: Final[str] = "delegation_brief"


@dataclass(frozen=True)
class DelegationContract:
    objective: str
    output_format: str
    tool_guidance: str
    task_boundaries: str

    def validate(self) -> list[str]:
        """Names of the fields that are blank."""
        return [
            name
            for name, value in vars(self).items()
            if not isinstance(value, str) or not value.strip()
        ]

    def render(self) -> str:
        """A delimited prompt block.

        Each field is one escaped line: markup cannot close the block and a
        line break cannot start a forged ``name:`` line.
        """
        lines = "\n".join(
            f"{name}: {escape_for_delimited_prompt(' '.join(value.splitlines()))}"
            for name, value in vars(self).items()
        )
        return f"<delegation_brief>\n{lines}\n</delegation_brief>"


def brief_for_worker(worker: "WorkerAssignment") -> DelegationContract:
    schema = json.dumps(dict(worker.output_schema), sort_keys=True, default=str)
    return DelegationContract(
        # A blank objective would invalidate the whole brief and drop it silently.
        objective=worker.objective.strip() or "Complete the assigned task.",
        output_format=(
            f"Output must satisfy this JSON Schema: {schema}"
            if worker.output_schema
            else "Free-form text"
        ),
        tool_guidance=(
            f"Tools you may call: {', '.join(worker.tool_allowlist)}"
            if worker.tool_allowlist
            else "No tools are available; answer from the request and upstream data"
        ),
        task_boundaries=(
            f"Act only within these permission scopes: {', '.join(worker.permission_scopes)}"
            if worker.permission_scopes
            else "No permission scopes are granted; stay within the request"
        ),
    )
