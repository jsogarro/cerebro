"""J5: manuscript upload, grammar findings, critique, upload as run evidence."""

import asyncio
import hashlib

import pytest

from tests.e2e.journeys.harness import require_path_containing

pytestmark = pytest.mark.e2e

MANUSCRIPT = b"We was testing the claim. The result are clear.\n"


def _missing(packet: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"{packet}: {what}"
    )


async def test_manuscript_upload_is_stored(client, new_account):
    alice = await new_account("alice")
    response = await client.post(
        "/api/v1/documents",
        headers=alice.headers,
        files={"file": ("manuscript.txt", MANUSCRIPT, "text/plain")},
    )
    assert response.status_code == 201, response.status_code
    body = response.json()
    assert body["text_sha256"] == hashlib.sha256(MANUSCRIPT).hexdigest()
    assert body["char_count"] == len(MANUSCRIPT.decode())
    assert "text" not in body


@_missing("P22", "no grammar route is served")
async def test_grammar_findings_have_correct_slices(client, new_account):
    await require_path_containing(client, await new_account("alice"), "grammar")
    pytest.fail("grammar landed: assert each finding's slice equals the quoted text")


async def test_critique_runs_on_a_fixture_model(client, new_account):
    alice = await new_account("alice")
    config = (await client.get("/api/v1/critique-config", headers=alice.headers)).json()
    uploaded = await client.post(
        "/api/v1/documents",
        headers=alice.headers,
        files={"file": ("manuscript.txt", MANUSCRIPT, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]

    consent = config["consents"][0]
    started = await client.post(
        f"/api/v1/documents/{document_id}/critiques",
        headers=alice.headers,
        json={
            "model_id": config["model_id"],
            "hosts": config["hosts"],
            "consent_text_version": consent["version"],
            "acknowledged": True,
        },
    )
    assert started.status_code == 202, started.text
    critique_id = started.json()["id"]

    critique = {}
    for _ in range(60):
        critique = (
            await client.get(f"/api/v1/critiques/{critique_id}", headers=alice.headers)
        ).json()
        if critique["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.5)
    assert critique["status"] == "succeeded", critique
    assert critique["findings"]
    text = MANUSCRIPT.decode()
    for finding in critique["findings"]:
        assert text[finding["raw_start"] : finding["raw_end"]] == finding["quote"]


@_missing("P12", "uploads cannot be attached to a run until the retention rule lands")
async def test_upload_is_usable_as_run_evidence(client, new_account):
    await require_path_containing(
        client, await new_account("alice"), "/documents/{document_id}/evidence"
    )
    pytest.fail("evidence landed: assert the run's evidence cites the upload digest")
