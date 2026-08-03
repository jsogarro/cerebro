"""Shared primitives for the versioned canonical contracts."""

import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.functional_serializers import PlainSerializer
from pydantic.functional_validators import AfterValidator, BeforeValidator

ContractSchemaVersion: TypeAlias = Literal["1.0"]
ContractId: TypeAlias = Annotated[str, Field(min_length=1, max_length=255)]
Version: TypeAlias = Annotated[str, Field(min_length=1, max_length=100)]
IdempotencyKey: TypeAlias = Annotated[str, Field(min_length=1, max_length=512)]
ContentSha256: TypeAlias = Annotated[
    str,
    Field(pattern=r"^[a-f0-9]{64}$"),
]


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _thaw_json_object(value: object) -> object:
    return _thaw_json_value(value)


def _freeze_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        frozen_mapping = MappingProxyType(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
        return cast(JsonValue, frozen_mapping)
    if isinstance(value, list):
        frozen_sequence = tuple(_freeze_json_value(item) for item in value)
        return cast(JsonValue, frozen_sequence)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    return value


def _freeze_json_object(
    value: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    return MappingProxyType(
        {key: _freeze_json_value(item) for key, item in value.items()}
    )


def _serialize_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _serialize_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_json_value(item) for item in value]
    return value


def _serialize_json_object(
    value: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {key: _serialize_json_value(item) for key, item in value.items()}


JsonObject: TypeAlias = Annotated[
    Mapping[str, JsonValue],
    BeforeValidator(_thaw_json_object),
    AfterValidator(_freeze_json_object),
    PlainSerializer(
        _serialize_json_object,
        return_type=dict[str, JsonValue],
    ),
]


class ContractModel(BaseModel):
    """Closed, immutable base for contracts serialized across boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    schema_version: ContractSchemaVersion = "1.0"

    def canonical_json(self) -> str:
        """Return deterministic JSON suitable for hashing and boundary records."""

        return json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def require_unique(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    """Reject duplicate stable references while preserving declared order."""

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} entries must be unique")
    return values
