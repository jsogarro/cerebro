"""Pure unit tests for the shared tenant write-path enforcement."""

import uuid

import pytest

from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
    enforce_tenant_identity,
    normalize_organization_id,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def test_normalize_organization_id_accepts_a_uuid_instance() -> None:
    assert normalize_organization_id(ORG_ID) == ORG_ID


def test_normalize_organization_id_accepts_a_uuid_string() -> None:
    assert normalize_organization_id(str(ORG_ID)) == ORG_ID


def test_normalize_organization_id_fails_closed_on_missing_context() -> None:
    with pytest.raises(MissingOrganizationContextError):
        normalize_organization_id(None)


def test_normalize_organization_id_rejects_a_non_uuid_string() -> None:
    with pytest.raises(ValueError):
        normalize_organization_id("not-a-uuid")


def test_enforce_tenant_identity_accepts_matching_identity() -> None:
    enforce_tenant_identity(tenant_id=str(ORG_ID), organization_id=ORG_ID)


def test_enforce_tenant_identity_normalizes_formatting_differences() -> None:
    # Same UUID, different casing/no dashes — must still match.
    enforce_tenant_identity(
        tenant_id=str(ORG_ID).upper().replace("-", ""), organization_id=ORG_ID
    )


def test_enforce_tenant_identity_rejects_mismatched_uuid() -> None:
    other = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    with pytest.raises(TenantMismatchError):
        enforce_tenant_identity(tenant_id=str(other), organization_id=ORG_ID)


def test_enforce_tenant_identity_rejects_a_non_uuid_tenant_id() -> None:
    with pytest.raises(TenantMismatchError):
        enforce_tenant_identity(tenant_id="tenant-1", organization_id=ORG_ID)
