"""The single seam that assigns an ingestion trust label, and license capture.

Packet 4A's non-guarantee 3: the initial trust label is *asserted*, not derived
— propagation is total and order-independent, but nothing checked that content
labelled ``application`` came from the application. Its stated fix was to
assign labels at one acquisition seam, keyed by source type, never from a
caller-supplied argument. These tests pin the two properties that makes that
real: the mapping is **total** over a closed enum, so a new source type cannot
be added without deciding its label, and its **codomain excludes the trusted
tier**, so no source type names a label that could be read as control.
"""

from enum import StrEnum

import pytest

from src.core.acquisition.sources import (
    LicenseDeclaration,
    SourceLicense,
    SourceType,
    trust_for_source,
)
from src.core.contracts.trust import TrustClassification, is_trusted_tier


class TestTrustAssignment:
    def test_every_source_type_has_a_label(self) -> None:
        # Totality is the guarantee. A missing entry would surface as a
        # KeyError at acquisition time, or worse, as a default nobody chose.
        for source_type in SourceType:
            assert isinstance(trust_for_source(source_type), TrustClassification)

    def test_no_source_type_is_labelled_into_the_trusted_tier(self) -> None:
        # This is what makes the caller's declaration harmless. Whatever source
        # type it names, the content it gets back is data, never control.
        for source_type in SourceType:
            assert not is_trusted_tier(trust_for_source(source_type))

    def test_a_fetched_page_is_external_untrusted(self) -> None:
        assert (
            trust_for_source(SourceType.WEB_PAGE)
            is TrustClassification.EXTERNAL_UNTRUSTED
        )

    def test_an_uploaded_document_is_user_supplied(self) -> None:
        assert (
            trust_for_source(SourceType.UPLOADED_DOCUMENT)
            is TrustClassification.USER_SUPPLIED
        )

    def test_a_string_that_looks_like_a_source_type_is_refused(self) -> None:
        # A plain string is how a caller would reach past the closed enum. The
        # value below equals SourceType.WEB_PAGE under StrEnum comparison, so
        # accepting strings would make the enum decorative.
        with pytest.raises(TypeError, match="SourceType"):
            trust_for_source("web_page")  # type: ignore[arg-type]

    def test_a_foreign_enum_member_is_refused(self) -> None:
        class Impostor(StrEnum):
            WEB_PAGE = "web_page"

        with pytest.raises(TypeError, match="SourceType"):
            trust_for_source(Impostor.WEB_PAGE)  # type: ignore[arg-type]


class TestLicenseCapture:
    def test_a_declared_license_records_what_declared_it(self) -> None:
        licence = SourceLicense(
            identifier="CC-BY-4.0",
            declared_by=LicenseDeclaration.HTTP_HEADER,
            statement="Creative Commons Attribution 4.0",
        )

        assert licence.as_metadata() == {
            "identifier": "CC-BY-4.0",
            "declared_by": "http_header",
            "statement": "Creative Commons Attribution 4.0",
        }

    def test_an_undeclared_license_is_recorded_explicitly(self) -> None:
        # "No license" must be a typed statement rather than an absent key.
        # A missing key reads as "nobody looked"; this reads as "we looked and
        # the source said nothing", which is what a reuse decision needs.
        licence = SourceLicense.undeclared()

        assert licence.as_metadata() == {
            "identifier": None,
            "declared_by": "undeclared",
            "statement": None,
        }

    def test_an_undeclared_license_cannot_name_one(self) -> None:
        with pytest.raises(ValueError, match="undeclared"):
            SourceLicense(identifier="MIT", declared_by=LicenseDeclaration.UNDECLARED)

    def test_a_declared_license_must_name_one(self) -> None:
        # Otherwise "the header said something" and "the header said nothing"
        # both round-trip to a null identifier and become indistinguishable.
        with pytest.raises(ValueError, match="names"):
            SourceLicense(identifier=None, declared_by=LicenseDeclaration.META_TAG)

    def test_an_undeclared_license_is_not_a_permissive_one(self) -> None:
        assert SourceLicense.undeclared().identifier is None
        assert not SourceLicense.undeclared().permits_redistribution_by_default
