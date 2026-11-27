"""J5: manuscript upload, grammar findings, critique, upload as run evidence."""

import pytest

from tests.e2e.journeys.harness import require_path_containing

pytestmark = pytest.mark.e2e

MANUSCRIPT = b"We was testing the claim. The result are clear.\n"


def _missing(packet: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"{packet}: {what}"
    )


@_missing(
    "P12",
    "POST /api/v1/documents is disabled (DOCUMENT_UPLOADS_ENABLED) in the default stack",
)
async def test_manuscript_upload_is_stored(client, new_account):
    alice = await new_account("alice")
    response = await client.post(
        "/api/v1/documents",
        headers=alice.headers,
        files={"file": ("manuscript.txt", MANUSCRIPT, "text/plain")},
    )
    assert response.status_code == 201, response.status_code
    pytest.fail(
        "uploads landed: assert text_sha256 and char_count match the manuscript"
    )


@_missing("P22", "no grammar route is served")
async def test_grammar_findings_have_correct_slices(client, new_account):
    await require_path_containing(client, await new_account("alice"), "grammar")
    pytest.fail("grammar landed: assert each finding's slice equals the quoted text")


@_missing(
    "P12",
    "a critique needs an uploaded document; uploads are disabled in the default stack",
)
async def test_critique_runs_on_a_fixture_model(client, new_account):
    alice = await new_account("alice")
    await require_path_containing(client, alice, "critique")
    response = await client.post(
        "/api/v1/documents",
        headers=alice.headers,
        files={"file": ("manuscript.txt", MANUSCRIPT, "text/plain")},
    )
    assert response.status_code == 201, response.status_code
    pytest.fail(
        "uploads landed: start a consented critique with a fixture model and "
        "assert its findings quote located spans"
    )


@_missing("P12", "uploads cannot be attached to a run until the retention rule lands")
async def test_upload_is_usable_as_run_evidence(client, new_account):
    await require_path_containing(
        client, await new_account("alice"), "/documents/{document_id}/evidence"
    )
    pytest.fail("evidence landed: assert the run's evidence cites the upload digest")
