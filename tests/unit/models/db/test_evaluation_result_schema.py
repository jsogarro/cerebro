"""``agent_evaluation_results`` declares the invariants the contract states."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.db.append_only import AppendOnlyViolationError
from src.models.db.base import Base
from src.models.db.evaluation_result import AgentEvaluationResult
from tests.unit.models.db.evaluation_result_cases import VIOLATIONS, valid_row


@pytest.fixture(name="engine")
def engine_fixture() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[AgentEvaluationResult.__table__])
    yield engine
    engine.dispose()


@pytest.fixture(name="session")
def session_fixture(engine: Engine) -> Iterator[Session]:
    with sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


def test_a_valid_row_persists(session: Session) -> None:
    session.add(AgentEvaluationResult(**valid_row()))
    session.commit()


@pytest.mark.parametrize(("constraint", "overrides"), VIOLATIONS)
def test_each_check_rejects_its_violation(
    session: Session, constraint: str, overrides: dict[str, object]
) -> None:
    session.add(AgentEvaluationResult(**valid_row(**overrides)))
    with pytest.raises(IntegrityError, match=constraint):
        session.commit()


def test_a_row_cannot_be_updated(session: Session) -> None:
    row = AgentEvaluationResult(**valid_row())
    session.add(row)
    session.commit()
    row.status = "failed"
    with pytest.raises(AppendOnlyViolationError):
        session.commit()


def test_result_ids_are_unique(session: Session) -> None:
    session.add(AgentEvaluationResult(**valid_row()))
    session.commit()
    session.add(AgentEvaluationResult(**valid_row()))
    with pytest.raises(IntegrityError):
        session.commit()
