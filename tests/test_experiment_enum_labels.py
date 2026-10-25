"""Experiment enum columns must bind member values, matching the migration's labels."""

import pytest

from src.models.db.experiment import (
    AllocationStrategy,
    Experiment,
    ExperimentStatus,
    ExperimentType,
)


@pytest.mark.parametrize(
    ("column", "enum"),
    [
        ("experiment_type", ExperimentType),
        ("status", ExperimentStatus),
        ("allocation_strategy", AllocationStrategy),
    ],
)
def test_enum_column_labels_are_member_values(column, enum) -> None:
    assert Experiment.__table__.c[column].type.enums == [m.value for m in enum]
