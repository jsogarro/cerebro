"""The quarantined, content-addressed store that holds verbatim source bytes.

The redaction contract calls the snapshot store "a quarantined zone: unredacted
at rest, redacted on every read that leaves it". These tests pin the three
properties that makes true rather than aspirational: content addressing, so a
snapshot cannot be replaced by different bytes under the same name; write-once
immutability, so a stored snapshot cannot move out from under a locator; and a
read that refuses any path outside the store, so a ``storage_uri`` read back
from a row cannot be turned into an arbitrary file read.
"""

from pathlib import Path

import pytest

from src.core.acquisition.snapshots import (
    FilesystemSnapshotStore,
    SnapshotIntegrityError,
    SnapshotNotFoundError,
)
from src.core.contracts.redaction import snapshot_digest

CONTENT = b"the source said this, verbatim\n"


@pytest.fixture(name="store")
def store_fixture(tmp_path: Path) -> FilesystemSnapshotStore:
    return FilesystemSnapshotStore(root=tmp_path / "snapshots")


class TestRoundTrip:
    def test_stored_bytes_come_back_byte_identical(
        self, store: FilesystemSnapshotStore
    ) -> None:
        uri = store.put(CONTENT)

        assert store.get(uri) == CONTENT

    def test_the_uri_addresses_the_content_digest(
        self, store: FilesystemSnapshotStore
    ) -> None:
        uri = store.put(CONTENT)

        assert snapshot_digest(CONTENT) in uri

    def test_storing_the_same_bytes_twice_yields_the_same_uri(
        self, store: FilesystemSnapshotStore
    ) -> None:
        assert store.put(CONTENT) == store.put(CONTENT)

    def test_different_bytes_get_different_uris(
        self, store: FilesystemSnapshotStore
    ) -> None:
        assert store.put(CONTENT) != store.put(b"something else entirely\n")

    def test_binary_content_survives_unchanged(
        self, store: FilesystemSnapshotStore
    ) -> None:
        # Nothing here decodes, normalizes line endings, or re-encodes. A PDF
        # must come back as the same bytes a locator's offsets were taken over.
        binary = bytes(range(256))

        assert store.get(store.put(binary)) == binary

    def test_a_second_store_over_the_same_root_reads_the_same_snapshot(
        self, tmp_path: Path
    ) -> None:
        # Nothing is held in memory: a store object is a view over a directory,
        # which is what lets a later process resolve an old locator.
        uri = FilesystemSnapshotStore(root=tmp_path / "s").put(CONTENT)

        assert FilesystemSnapshotStore(root=tmp_path / "s").get(uri) == CONTENT


class TestImmutability:
    def test_a_stored_snapshot_is_not_writable(
        self, store: FilesystemSnapshotStore
    ) -> None:
        path = Path(store.put(CONTENT).removeprefix("file://"))

        with pytest.raises(PermissionError):
            path.open("wb")

    def test_a_snapshot_whose_bytes_no_longer_hash_to_its_name_is_refused(
        self, store: FilesystemSnapshotStore
    ) -> None:
        # The digest is re-checked on read, so tampering that got past the
        # filesystem is caught before the bytes are used as evidence rather
        # than after a locator has already been resolved against them.
        uri = store.put(CONTENT)
        path = Path(uri.removeprefix("file://"))
        path.chmod(0o644)
        path.write_bytes(b"tampered")

        with pytest.raises(SnapshotIntegrityError, match="does not hash"):
            store.get(uri)


class TestRefusals:
    def test_reading_an_absent_snapshot_raises(
        self, store: FilesystemSnapshotStore
    ) -> None:
        with pytest.raises(SnapshotNotFoundError):
            store.get(f"file://{store.root / 'ab' / ('a' * 64)}")

    def test_a_uri_outside_the_store_root_is_refused(
        self, store: FilesystemSnapshotStore, tmp_path: Path
    ) -> None:
        # A storage_uri is read back out of a database row. Treating it as a
        # path to open without this check turns any write to that column into
        # an arbitrary file read.
        outside = tmp_path / "secrets.txt"
        outside.write_bytes(b"not a snapshot")

        with pytest.raises(SnapshotIntegrityError, match="outside"):
            store.get(f"file://{outside}")

    def test_a_traversal_uri_is_refused(self, store: FilesystemSnapshotStore) -> None:
        with pytest.raises(SnapshotIntegrityError, match="outside"):
            store.get(f"file://{store.root}/../../etc/passwd")

    def test_a_non_file_uri_is_refused(self, store: FilesystemSnapshotStore) -> None:
        with pytest.raises(SnapshotIntegrityError, match="scheme"):
            store.get("https://example.org/snapshot")

    def test_storing_nothing_is_refused(self, store: FilesystemSnapshotStore) -> None:
        # A zero-byte snapshot resolves no locator at all, so admitting one
        # only defers the failure to the point where it looks like missing
        # evidence rather than a failed acquisition.
        with pytest.raises(ValueError, match="empty"):
            store.put(b"")
