"""The quarantined store holding verbatim acquired bytes.

The redaction contract describes this store precisely: **unredacted at rest,
redacted on every read that leaves it**. It is the one place in the system that
deliberately holds unscrubbed external content, and it holds it because
everything evidential depends on it — ``snapshot_digest`` hashes these bytes,
and every locator's offsets are taken over them. Scrubbing here would change
what a source is recorded as having said *and* silently move every citation.

Three properties, each doing one job:

**Content addressing.** A snapshot's name is its own digest, so the same bytes
always land at the same address and different bytes never can. This is what
makes ``Evidence.content_sha256`` verifiable years later against nothing but
the file's own name.

**Write-once.** A stored snapshot is made read-only, and a second ``put`` of
identical content is a no-op rather than a rewrite. A snapshot that could be
edited in place would let every locator citing it silently come to mean
something else, which is exactly the failure the immutability requirement
exists to prevent.

**A read that refuses to leave the store.** ``storage_uri`` is read back out of
a database row. Opening whatever path that column happens to contain would turn
any write to it into an arbitrary file read, so ``get`` resolves the path and
refuses anything that is not inside this store's own root — and then re-checks
the digest, so tampering that got past the filesystem is caught *before* the
bytes are used as evidence rather than after a locator has resolved against
them.
"""

import os
import tempfile
from pathlib import Path
from typing import Protocol, final, runtime_checkable
from urllib.parse import unquote, urlparse

from src.core.contracts.redaction import snapshot_digest

__all__ = [
    "FilesystemSnapshotStore",
    "SnapshotIntegrityError",
    "SnapshotNotFoundError",
    "SnapshotStore",
]

_READ_ONLY = 0o444
_DIRECTORY = 0o755


class SnapshotIntegrityError(ValueError):
    """Raised when a snapshot is not the content its address claims.

    Covers both a digest that disagrees with the stored bytes and a
    ``storage_uri`` that does not name a location inside the store. Both mean
    the same thing to a caller: the bytes in hand cannot be trusted to be the
    ones the evidence was taken over, so nothing may be resolved against them.
    """


class SnapshotNotFoundError(LookupError):
    """Raised when no snapshot is stored at a well-formed, in-store address."""


@runtime_checkable
class SnapshotStore(Protocol):
    """Immutable storage for verbatim acquired bytes."""

    def put(self, data: bytes) -> str:
        """Store ``data`` immutably and return its ``storage_uri``."""
        ...

    def get(self, storage_uri: str) -> bytes:
        """Return the verbatim bytes stored at ``storage_uri``."""
        ...


@final
class FilesystemSnapshotStore:
    """A content-addressed snapshot store over a local directory tree.

    Layout is ``<root>/<first two hex digits>/<full digest>``. The fan-out
    keeps a single directory from accumulating every snapshot a deployment ever
    took; nothing else depends on it, so a different backend can address
    content however it likes.
    """

    __slots__ = ("_root",)

    def __init__(self, *, root: Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def put(self, data: bytes) -> str:
        """Store ``data`` under its own digest and return its ``storage_uri``.

        Storing identical bytes again is a no-op that returns the same URI:
        content addressing makes a rewrite pointless, and *not* rewriting is
        what keeps an existing snapshot immutable even against a caller that
        re-acquires the same source.

        Raises:
            ValueError: ``data`` is empty. A zero-byte snapshot resolves no
                locator at all, so admitting one only defers the failure to a
                point where it reads as missing evidence rather than as a
                failed acquisition.
            SnapshotIntegrityError: A file already occupies this address with
                different content, which content addressing makes impossible
                and therefore means the store is corrupt.
        """

        if not isinstance(data, bytes | bytearray):
            raise TypeError("a snapshot is stored as the bytes that were acquired")
        if not data:
            raise ValueError(
                "refusing to store an empty snapshot; it can never resolve a "
                "locator, so it is a failed acquisition rather than a source"
            )

        digest = snapshot_digest(bytes(data))
        path = self._path_for(digest)
        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                raise SnapshotIntegrityError(
                    f"snapshot {digest} already exists holding different bytes; "
                    "the store is corrupt and no locator over it can be trusted"
                )
            return _to_uri(path)

        path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY)
        self._write_once(path, bytes(data))
        return _to_uri(path)

    def get(self, storage_uri: str) -> bytes:
        """Return the verbatim bytes at ``storage_uri``, or refuse.

        Raises:
            SnapshotIntegrityError: The URI is not a ``file://`` location
                inside this store, or the stored bytes do not hash to the
                address they are stored at.
            SnapshotNotFoundError: Nothing is stored at that address.
        """

        path = self._resolve_uri(storage_uri)
        try:
            data = path.read_bytes()
        except FileNotFoundError as error:
            raise SnapshotNotFoundError(
                f"no snapshot is stored at {storage_uri!r}"
            ) from error

        digest = snapshot_digest(data)
        if digest != path.name:
            raise SnapshotIntegrityError(
                f"snapshot at {storage_uri!r} does not hash to the address it is "
                f"stored at ({digest} != {path.name}); refusing to resolve "
                "evidence against content that has changed"
            )
        return data

    def _path_for(self, digest: str) -> Path:
        return self._root / digest[:2] / digest

    def _resolve_uri(self, storage_uri: str) -> Path:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "file":
            raise SnapshotIntegrityError(
                f"{storage_uri!r} does not use the file scheme this store "
                "addresses; a snapshot is never fetched from a URI at read time"
            )
        candidate = Path(unquote(parsed.path)).resolve()
        if not candidate.is_relative_to(self._root):
            raise SnapshotIntegrityError(
                f"{storage_uri!r} resolves outside the snapshot store root "
                f"{self._root}; refusing to read it"
            )
        return candidate

    @staticmethod
    def _write_once(path: Path, data: bytes) -> None:
        """Write ``data`` and make it read-only, atomically.

        Written to a temporary file in the same directory and renamed, so a
        concurrent reader never observes a partial snapshot at an address whose
        name asserts a digest of the whole. ``os.replace`` is atomic within a
        filesystem; the temporary file is in the destination directory
        precisely to keep it one.
        """

        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, _READ_ONLY)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def _to_uri(path: Path) -> str:
    return f"file://{path}"
