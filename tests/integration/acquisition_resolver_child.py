"""Re-resolve one evidence locator in a genuinely new process.

Not a test module (no ``test_`` prefix): it is the child half of
``test_source_acquisition_round_trip.py``, executed with ``subprocess`` so the
word "fresh" needs no argument. Packet 4A named the hazard in its own
acceptance test — "acquire, locate, re-resolve in a fresh session" passes
trivially if the session is not actually fresh or if the locator is re-derived
rather than re-resolved. A separate interpreter removes both possibilities
structurally rather than by inspection: no engine, no connection, no session,
no identity map, no store object, and no Python object of any kind survives a
process boundary.

Its entire input is three strings on ``argv`` — a database URL, a snapshot
store root, and an evidence id. In particular it is **not** given the expected
excerpt, so it cannot echo an answer it was handed; the parent asserts that
too, rather than trusting this docstring.

Everything it needs comes back out of durable state: the locator is read from
the row **as a string**, the snapshot bytes are read from the store by the
``storage_uri`` on the artifact the row cites, and the digest is checked before
resolution. Exits non-zero on any missing row, so an empty read can never be
mistaken for agreement.
"""

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.acquisition.resolution import resolve
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.contracts.redaction import snapshot_digest
from src.models.db.artifact import AgentArtifact
from src.models.db.evidence import AgentEvidence


async def _resolve(database_url: str, store_root: str, evidence_id: str) -> dict:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            evidence = (
                await session.execute(
                    select(AgentEvidence).where(
                        AgentEvidence.evidence_id == evidence_id
                    )
                )
            ).scalar_one_or_none()
            if evidence is None:
                raise SystemExit(f"no evidence row {evidence_id!r} in this database")

            # Read out as plain values inside the session, so nothing below
            # depends on a live ORM instance.
            locator = str(evidence.locator)
            content_sha256 = str(evidence.content_sha256)
            snapshot_artifact_id = str(evidence.snapshot_artifact_id)

            artifact = (
                await session.execute(
                    select(AgentArtifact).where(
                        AgentArtifact.artifact_id == snapshot_artifact_id
                    )
                )
            ).scalar_one_or_none()
            if artifact is None:
                raise SystemExit(
                    f"evidence {evidence_id!r} cites artifact "
                    f"{snapshot_artifact_id!r}, which is not in this database"
                )
            storage_uri = str(artifact.storage_uri)
    finally:
        await engine.dispose()

    data = FilesystemSnapshotStore(root=Path(store_root)).get(storage_uri)
    if snapshot_digest(data) != content_sha256:
        raise SystemExit(
            f"snapshot for evidence {evidence_id!r} does not hash to the digest "
            "the row cites"
        )

    return {
        "locator": locator,
        "content_sha256": content_sha256,
        "storage_uri": storage_uri,
        "excerpt_hex": resolve(locator, data).hex(),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: acquisition_resolver_child DATABASE_URL STORE_ROOT EVIDENCE_ID",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(asyncio.run(_resolve(argv[1], argv[2], argv[3]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
