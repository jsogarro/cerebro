"""The filters an academic search may carry, validated before any request.

Filters used to be an open dict that adapters read key by key, so an
unsupported filter was acknowledged and silently ignored. Here the set is
closed: anything outside it, or a value a provider cannot honour, is refused
before the first request is dispatched.
"""

from collections.abc import Sequence
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Categories are interpolated into an arXiv boolean query, so the grammar is
# restricted to what a category name is rather than escaped.
Category = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,31}$")]

ARXIV: Final = "arxiv"


class AcademicSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year_start: int | None = Field(default=None, ge=1000, le=9999)
    year_end: int | None = Field(default=None, ge=1000, le=9999)
    categories: tuple[Category, ...] = ()

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Self:
        if (
            self.year_start is not None
            and self.year_end is not None
            and self.year_start > self.year_end
        ):
            raise ValueError("year_start must not be after year_end")
        return self

    def require_supported_by(self, databases: Sequence[str]) -> Self:
        if self.categories and ARXIV not in databases:
            raise ValueError("categories filter is only supported by arxiv")
        return self

    @property
    def bounded(self) -> bool:
        return self.year_start is not None or self.year_end is not None

    def admits(self, year: int | None) -> bool:
        """Inclusive range check; an unknown year never satisfies a bound."""

        if not self.bounded:
            return True
        if year is None:
            return False
        return (self.year_start is None or year >= self.year_start) and (
            self.year_end is None or year <= self.year_end
        )

    def applied(self) -> dict[str, int | list[str]]:
        """What was actually constrained, for the caller to see."""

        out: dict[str, int | list[str]] = {}
        if self.year_start is not None:
            out["year_start"] = self.year_start
        if self.year_end is not None:
            out["year_end"] = self.year_end
        if self.categories:
            out["categories"] = list(self.categories)
        return out
