"""Append-only enforcement for immutable durable-execution records.

Some durable records are evidence rather than state: the run event stream and
the configuration snapshot a run was admitted under. Once written they must
never change, or replay and audit stop meaning anything.

This module blocks the ORM update and delete paths. It does not stop
``session.execute(update(...))`` or raw SQL, which bypass the ORM unit of work
entirely — the migration installs Postgres triggers for that. Both layers are
needed: the ORM guard makes the violation visible in tests and in application
code, the trigger makes it true in the database.
"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Mapper


class AppendOnlyViolationError(RuntimeError):
    """Raised when application code tries to change an immutable record."""


def register_append_only(model: type) -> type:
    """Reject ORM updates and deletes for ``model``.

    Usable as a class decorator so the guarantee sits next to the model it
    protects rather than in distant wiring.
    """

    def _reject(action: str) -> Any:
        def handler(mapper: Mapper[Any], connection: Any, target: Any) -> None:
            raise AppendOnlyViolationError(
                f"{type(target).__name__} is append-only; {action} is not permitted"
            )

        return handler

    event.listen(model, "before_update", _reject("update"), propagate=True)
    event.listen(model, "before_delete", _reject("delete"), propagate=True)
    return model
