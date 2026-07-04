"""Safe arithmetic expression evaluation tool.

Evaluates arithmetic expressions using AST parsing with a strict whitelist of
allowed nodes and operators. No eval(), no code execution, no injection risk.
"""

import ast
import operator
from typing import Any

from pydantic import BaseModel, Field

from src.agents.tools.base_tool import AgentTool


class ArithmeticParams(BaseModel):
    """Parameters for safe arithmetic evaluation."""

    expression: str = Field(
        ..., description="Arithmetic expression (e.g., '(2 + 3) * 4 / 2')",
    )


class ArithmeticTool(AgentTool[ArithmeticParams]):
    """Safe arithmetic expression evaluator.

    Supports: +, -, *, /, **, //, %, unary +/-.
    Guards against: division by zero, huge exponents, code injection.
    """

    @property
    def name(self) -> str:
        return "arithmetic"

    @property
    def description(self) -> str:
        return "Safe evaluation of arithmetic expressions"

    @property
    def params_model(self) -> type[ArithmeticParams]:
        return ArithmeticParams

    # Safe operators mapping AST nodes to operator functions
    _OPERATORS: dict[type[ast.operator | ast.unaryop], Any] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    # Maximum exponent to prevent huge computations
    _MAX_EXPONENT = 1000

    def _eval_node(self, node: ast.AST) -> float:
        """Recursively evaluate an AST node.

        Raises ValueError for disallowed nodes, division by zero, or huge exponents.
        """
        if isinstance(node, ast.Num):  # Number literal (3.14)
            n_val = node.n
            if not isinstance(n_val, (int, float)):
                raise ValueError(f"Unsupported number type: {type(n_val)}")
            return float(n_val)
        if isinstance(node, ast.Constant):  # Constant (Python 3.8+)
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"Unsupported constant: {type(node.value)}")
        if isinstance(node, ast.BinOp):  # Binary operation (x + y)
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            op = self._OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")

            # Guard against division by zero
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValueError("Division by zero")

            # Guard against huge exponents
            if isinstance(node.op, ast.Pow) and abs(right) > self._MAX_EXPONENT:
                raise ValueError(
                    f"Exponent too large: {right} (max ±{self._MAX_EXPONENT})",
                )

            bin_result: float = float(op(left, right))
            return bin_result
        if isinstance(node, ast.UnaryOp):  # Unary operation (+x, -x)
            operand = self._eval_node(node.operand)
            op = self._OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(
                    f"Unsupported unary operator: {type(node.op).__name__}",
                )
            unary_result: float = float(op(operand))
            return unary_result
        raise ValueError(
            f"Disallowed node type: {type(node).__name__}. "
            "Only arithmetic expressions are allowed.",
        )

    async def _execute_impl(self, params: ArithmeticParams) -> Any:
        """Parse and evaluate the arithmetic expression safely."""
        expression = params.expression.strip()
        if not expression:
            raise ValueError("Expression cannot be empty")

        # Parse into AST
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"Invalid expression syntax: {exc}") from exc

        # Evaluate the expression tree
        result = self._eval_node(tree.body)

        return {"expression": expression, "result": round(result, 10)}


__all__ = ["ArithmeticParams", "ArithmeticTool"]
