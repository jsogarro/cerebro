"""The API the e2e stack serves: the real app with the review model replaced.

Only the e2e compose override points uvicorn here, so no deployment can serve a
fixture model by accident, and the critique journey makes no provider call.
"""

import json

from src.agents.critique.model import ModelReply
from src.api.main import app
from src.api.routes.critiques import get_review_model

_REPLY = json.dumps(
    {
        "issues": [
            {
                "quote": "We was testing the claim.",
                "problem": "The verb does not agree with its subject.",
                "severity": "minor",
                "fix": "Write 'We were testing the claim.'",
                "category": "other",
            }
        ],
        "summary": "fixture",
        "complete": True,
    }
)


class FixtureReviewModel:
    model_id = "gemini-2.5-flash"
    temperature = 0.0

    async def complete(self, prompt: str) -> ModelReply:
        return ModelReply(_REPLY, "STOP", 10, 5)


app.dependency_overrides[get_review_model] = lambda: FixtureReviewModel
