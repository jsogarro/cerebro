"""remember_me is accepted only as false until a session-duration policy exists."""

import pytest
from pydantic import ValidationError

from src.auth.models import LoginRequest

BASE = {"email": "user@example.com", "password": "SecurePassword123!"}


@pytest.mark.parametrize("extra", [{}, {"remember_me": False}])
def test_omitted_or_false_remember_me_is_accepted(extra):
    assert LoginRequest(**BASE, **extra).remember_me is False


def test_true_remember_me_is_rejected_with_a_stable_error():
    with pytest.raises(ValidationError) as caught:
        LoginRequest(**BASE, remember_me=True)
    (error,) = caught.value.errors()
    assert error["loc"] == ("remember_me",)
    assert "not supported" in error["msg"]
