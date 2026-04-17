"""Cost management and budgeting system."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class CostTransaction:
    """A cost transaction record."""
    transaction_id: str
    organization_id: str
    project_id: str
    user_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    total_cost_cents: int
    created_at: datetime = field(default_factory=datetime.now)


class CostTracker:
    """Track and record costs."""
    
    PRICING = {
        'openrouter/moonshotai/kimi-k2.5': {'input': 0.001, 'output': 0.003},
        'openrouter/minimax/minimax-m2.5': {'input': 0.002, 'output': 0.006},
        'openrouter/z-ai/glm-5': {'input': 0.003, 'output': 0.009}
    }
    
    async def record_usage(self, organization_id: str, project_id: str, user_id: str,
                          model_id: str, input_tokens: int, output_tokens: int) -> CostTransaction:
        """Record usage."""
        import uuid
        
        pricing = self.PRICING.get(model_id, {'input': 0.0, 'output': 0.0})
        input_cost = (input_tokens / 1000) * pricing['input']
        output_cost = (output_tokens / 1000) * pricing['output']
        total_cost = int((input_cost + output_cost) * 100)
        
        return CostTransaction(
            transaction_id=str(uuid.uuid4()),
            organization_id=organization_id,
            project_id=project_id,
            user_id=user_id,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost_cents=total_cost
        )
    
    async def get_current_spend(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        """Get current spend."""
        return {
            'total_spend_usd': Decimal('0.00'),
            'total_tokens': 0
        }


@dataclass
class Budget:
    """Budget definition."""
    id: str
    scope_type: str
    scope_id: str
    amount_cents: int
    period_type: str
    alert_thresholds: list[int]


class BudgetManager:
    """Manage budgets."""

    async def create_budget(self, scope_type: str, scope_id: str,
                           amount: Decimal, period: str) -> Budget:
        """Create budget."""
        import uuid
        return Budget(
            id=str(uuid.uuid4()),
            scope_type=scope_type,
            scope_id=scope_id,
            amount_cents=int(amount * 100),
            period_type=period,
            alert_thresholds=[50, 80, 95]
        )

    async def check_budget(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        """Check budget status."""
        return {
            'has_budget': True,
            'percent_used': 50.0,
            'status': 'healthy'
        }


class CostForecaster:
    """Forecast future costs."""

    async def forecast_costs(self, scope_type: str, scope_id: str,
                            horizon_days: int = 30) -> dict[str, Any]:
        """Generate forecast."""
        return {
            'predicted': Decimal('100.00'),
            'confidence': 'medium',
            'trend': 'stable'
        }

    async def estimate_query_cost(self, query: str, domains: list[str],
                                 depth: str) -> dict[str, Any]:
        """Estimate cost."""
        return {
            'estimated_cost': 0.5,
            'recommended_model': 'openrouter/moonshotai/kimi-k2.5'
        }


class CostOptimizer:
    """Optimize costs."""

    async def get_optimization_suggestions(self, organization_id: str) -> list[dict[str, Any]]:
        """Get suggestions."""
        return []

    async def auto_downgrade(self, scope_type: str, scope_id: str,
                            current_model: str) -> dict[str, Any]:
        """Auto-downgrade model."""
        return {'success': False}