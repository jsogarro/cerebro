"""The typed shape of one academic search hit and of what recording it did.

``RetrievedSource`` is the single reading of a provider record: which source it
names, what text it contributes, and what bibliographic identity it carries.
Nothing is synthesized; a field the provider did not supply stays absent.
``SourceRecordingOutcome`` is what the recorder reports per source, so a caller
can tell a recorded source from a skipped or failed one and, on replay, still
learns the stable evidence id.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, JsonValue

__all__ = [
    "MAX_SOURCE_URI_LENGTH",
    "RetrievedSource",
    "SourceDisposition",
    "SourceRecordingOutcome",
    "source_text",
    "source_uri",
]

MAX_SOURCE_URI_LENGTH: Final[int] = 2048
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_YEAR = re.compile(r"^\d{4}$")


def source_text(source: Mapping[str, Any]) -> str:
    """The text a search record contributes: its title and abstract."""

    abstract = str(source.get("abstract") or "").strip()
    if not abstract:
        return ""
    title = str(source.get("title") or "").strip()
    return f"{title}\n\n{abstract}" if title else abstract


def source_uri(source: Mapping[str, Any]) -> str | None:
    """Name the record's source from an http(s) URL or a DOI, else ``None``.

    Provider-supplied strings are untrusted: anything else, or anything over
    the length cap, is refused rather than normalized.
    """

    url = str(source.get("url") or "").strip()
    if url:
        parts = urlsplit(url)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.netloc)
            and "@" not in parts.netloc  # userinfo can carry credentials
        )
        return url if valid and len(url) <= MAX_SOURCE_URI_LENGTH else None
    doi = str(source.get("doi") or "").strip()
    if _DOI.match(doi) and len(doi) <= MAX_SOURCE_URI_LENGTH - 16:
        return f"https://doi.org/{doi}"
    return None


def _year(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if _YEAR.match(text) else None


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


class RetrievedSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str | None = None
    provider_id: str | None = None
    title: str = ""
    abstract: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    doi: str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RetrievedSource":
        authors = record.get("authors")
        return cls(
            provider=_text(record.get("source")),
            provider_id=_text(record.get("id")),
            title=str(record.get("title") or "").strip(),
            abstract=str(record.get("abstract") or "").strip(),
            authors=tuple(
                str(a).strip()
                for a in (authors if isinstance(authors, Sequence) else ())
                if not isinstance(authors, str) and str(a).strip()
            ),
            year=_year(record.get("year")),
            venue=_text(record.get("venue") or record.get("journal")),
            url=_text(record.get("url")),
            doi=_text(record.get("doi")),
        )

    def text(self) -> str:
        return source_text(self.model_dump())

    def uri(self) -> str | None:
        return source_uri(self.model_dump())

    def bibliography(self) -> dict[str, JsonValue]:
        """Identity and citation fields kept with the snapshot, absent ones omitted."""

        fields: dict[str, JsonValue] = {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "title": self.title or None,
            "authors": list(self.authors) or None,
            "year": self.year,
            "venue": self.venue,
            "url": self.url,
            "doi": self.doi,
        }
        return {k: v for k, v in fields.items() if v is not None}


class SourceDisposition(StrEnum):
    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    SKIPPED = "skipped"
    FAILED = "failed"


class SourceRecordingOutcome(BaseModel):
    """What happened to the source at ``index`` of the search result."""

    model_config = ConfigDict(frozen=True)

    index: int
    disposition: SourceDisposition
    source_uri: str | None = None
    provider: str | None = None
    provider_id: str | None = None
    doi: str | None = None
    year: int | None = None
    evidence_id: str | None = None
    reason: str | None = None
