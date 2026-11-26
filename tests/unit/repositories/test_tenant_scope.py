"""Pure unit tests for the shared tenant write-path enforcement."""

import uuid

import pytest
from sqlalchemy import Integer, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.models.db.base import UUID
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
    enforce_tenant_identity,
    normalize_organization_id,
    scope_to_owner,
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


class _OwnedBase(DeclarativeBase):
    pass


class _Owned(_OwnedBase):
    __tablename__ = "owned"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID())
    user_id: Mapped[uuid.UUID] = mapped_column(UUID())


USER_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _visible_ids(org: uuid.UUID | None, user: uuid.UUID | None) -> list[int]:
    engine = create_engine("sqlite://")
    _OwnedBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                _Owned(id=1, organization_id=ORG_ID, user_id=USER_A),
                _Owned(id=2, organization_id=ORG_ID, user_id=USER_B),
                _Owned(id=3, organization_id=ORG_B, user_id=USER_A),
            ]
        )
        session.commit()
        query = scope_to_owner(
            select(_Owned.id),
            org_column=_Owned.organization_id,
            user_column=_Owned.user_id,
            organization_id=org,  # type: ignore[arg-type]
            user_id=user,  # type: ignore[arg-type]
        )
        return sorted(session.scalars(query))


def test_scope_to_owner_shows_only_the_owners_rows() -> None:
    assert _visible_ids(ORG_ID, USER_A) == [1]
    assert _visible_ids(ORG_ID, USER_B) == [2]
    assert _visible_ids(ORG_B, USER_A) == [3]
    assert _visible_ids(ORG_B, USER_B) == []


@pytest.mark.parametrize(("org", "user"), [(None, USER_A), (ORG_ID, None)])
def test_scope_to_owner_refuses_a_missing_identity(
    org: uuid.UUID | None, user: uuid.UUID | None
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        _visible_ids(org, user)
