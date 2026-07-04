"""Deterministic financial calculator agent.

Wraps the ``finance_math`` tool so exact financial calculations are available
through the agent API. Unlike the LLM finance agents, this agent does no
language-model reasoning — it computes precise numbers from structured inputs,
so results are exact and reproducible.

Invoke with ``parameters = {"operation": <op>, ...op-specific inputs...}``.
Operations: dcf, npv, ratios, compound_interest, amortization, stats.
"""

from typing import Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.agents.tools import finance_math


class FinancialCalculatorAgent(BaseAgent):
    """Exact financial/statistical calculations from structured parameters."""

    def get_agent_type(self) -> str:
        return "financial_calculator"

    async def execute(self, task: AgentTask) -> AgentResult:
        params: dict[str, Any] = task.input_data.get("parameters") or {}
        operation = params.get("operation")
        if not operation:
            return self.handle_error(
                task,
                ValueError(
                    "parameters.operation is required. Available: "
                    f"{finance_math.AVAILABLE_OPERATIONS}",
                ),
            )

        try:
            result = finance_math.calculate(operation, params)
        except ValueError as exc:
            return self.handle_error(task, exc)

        # Structured result (Pydantic models expose model_dump()).
        result_data = result.model_dump() if hasattr(result, "model_dump") else result

        return AgentResult(
            task_id=task.id,
            status="success",
            output={
                "operation": operation,
                "result": result_data,
                "agent_type": "financial_calculator",
            },
            confidence=1.0,  # deterministic
            execution_time=0.0,
            metadata=self.build_execution_metadata(operation=operation),
        )

    async def validate_result(self, result: AgentResult) -> bool:
        return result.status == "success" and "result" in result.output


__all__ = ["FinancialCalculatorAgent"]
