"""Shared tenant write-path enforcement for the durable lifecycle repositories.

The accepted Wave 3 tenant identity decision is one identity, two
representations: ``tenant_id`` (the contract-level string carried through the
event stream) and ``organization_id`` (the UUID column the database filters
on) must always agree. This module is the single place that agreement is
checked, so every repository that writes a tenant-scoped row enforces it the
same way instead of re-deriving the rule.

Reads stay permissive — callers that have no org context yet (a boot-time
recovery scan across every tenant) pass ``organization_id=None`` and get an
unscoped query. Writes are fail-closed: no org context is a hard error, not a
silently unscoped write, and there is no system/no-org escape hatch.
"""

import uuid


class MissingOrganizationContextError(ValueError):
    """Raised when a write is attempted without an authenticated org context.

    There is no NULL-organization escape hatch: a run, task, attempt, event,
    or config snapshot without a resolvable tenant is a bug at the call site,
    not a valid row.
    """


class TenantMismatchError(ValueError):
    """Raised when a contract's tenant_id disagrees with the organization context."""


def normalize_organization_id(organization_id: uuid.UUID | str | None) -> uuid.UUID:
    """Return ``organization_id`` as a ``UUID``, failing closed on missing context.

    Args:
        organization_id: The authenticated tenant boundary, or ``None`` if no
            org context was supplied.

    Returns:
        The organization id as a ``UUID``.

    Raises:
        MissingOrganizationContextError: ``organization_id`` is ``None``.
    """
    if organization_id is None:
        raise MissingOrganizationContextError(
            "a write requires an authenticated organization context"
        )
    if isinstance(organization_id, uuid.UUID):
        return organization_id
    return uuid.UUID(organization_id)


def enforce_tenant_identity(*, tenant_id: str, organization_id: uuid.UUID) -> None:
    """Reject a write whose contract tenant disagrees with the org context.

    ``tenant_id`` is normalized through a ``uuid.UUID(...)`` round-trip before
    comparison, per the accepted tenant identity decision, so formatting
    differences (case, missing dashes) never cause a false mismatch — and a
    ``tenant_id`` that is not a UUID at all is itself a mismatch.

    Args:
        tenant_id: The contract-level tenant identity (``Run.tenant_id`` or a
            value copied from it).
        organization_id: The authenticated organization boundary, already
            normalized by :func:`normalize_organization_id`.

    Raises:
        TenantMismatchError: ``tenant_id`` is not a valid UUID, or does not
            equal ``organization_id``.
    """
    try:
        normalized_tenant_id = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError) as error:
        raise TenantMismatchError(
            f"tenant_id {tenant_id!r} is not a valid organization identifier"
        ) from error
    if normalized_tenant_id != organization_id:
        raise TenantMismatchError(
            f"tenant_id {tenant_id!r} does not match organization {organization_id}"
        )


__all__ = [
    "MissingOrganizationContextError",
    "TenantMismatchError",
    "enforce_tenant_identity",
    "normalize_organization_id",
]
