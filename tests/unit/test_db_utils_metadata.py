"""The integration-test DB helper must build the schema the application models define."""

from src.models.db.base import Base as ModelBase
from tests.utils import db_utils


def test_create_tables_uses_the_metadata_the_models_register_on() -> None:
    assert db_utils.Base.metadata is ModelBase.metadata
    assert "users" in db_utils.Base.metadata.tables
