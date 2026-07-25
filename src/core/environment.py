"""Shared process-level environment loading.

Dotenv files are parsed as data. Existing process keys remain authoritative,
the home file may override values loaded from the project file, and explicit
empty values are preserved.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, MutableMapping
from pathlib import Path

from dotenv import dotenv_values
from dotenv.variables import parse_variables

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"
HOME_ENV_PATH = Path.home() / ".env"


class _InterpolationEnvironment(Mapping[str, str]):
    """Resolve merged dotenv references without consulting host state."""

    def __init__(
        self,
        *,
        process_values: Mapping[str, str],
        dotenv_values: Mapping[str, str],
    ) -> None:
        self._process_values = process_values
        self._dotenv_values = dotenv_values
        self._resolved: dict[str, str] = {}
        self._resolving: set[str] = set()

    def __getitem__(self, key: str) -> str:
        if key in self._process_values:
            return self._process_values[key]
        if key in self._resolved:
            return self._resolved[key]
        if key not in self._dotenv_values:
            raise KeyError(key)
        if key in self._resolving:
            # Match python-dotenv's missing-reference behavior while ensuring
            # a cross-file reference cycle terminates deterministically.
            return ""

        self._resolving.add(key)
        try:
            raw_value = self._dotenv_values[key]
            resolved_value = "".join(
                atom.resolve(self) for atom in parse_variables(raw_value)
            )
            self._resolved[key] = resolved_value
            return resolved_value
        finally:
            self._resolving.remove(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._process_values.keys() | self._dotenv_values.keys())

    def __len__(self) -> int:
        return len(self._process_values.keys() | self._dotenv_values.keys())


def load_environment(
    *,
    project_env_path: Path | None = None,
    home_env_path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Load project and home dotenv fallbacks into an environment mapping.

    Precedence is existing mapping values, then the home dotenv file, then the
    project dotenv file. Paths and the destination mapping are injectable so
    callers can test the behavior without accessing user dotenv files.
    """
    target = os.environ if environ is None else environ
    if target.get("CEREBRO_DISABLE_DOTENV", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    process_values = dict(target)
    original_keys = frozenset(process_values)
    project_path = project_env_path or PROJECT_ENV_PATH
    home_path = home_env_path or HOME_ENV_PATH

    merged_values: dict[str, str] = {}
    for path in (project_path, home_path):
        if not path.is_file():
            continue
        for key, value in dotenv_values(path, interpolate=False).items():
            if value is None:
                continue
            merged_values[key] = value

    interpolation_environment = _InterpolationEnvironment(
        process_values=process_values,
        dotenv_values=merged_values,
    )
    for key in merged_values:
        if key not in original_keys:
            target[key] = interpolation_environment[key]
