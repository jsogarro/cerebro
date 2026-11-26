"""J6: failed logins are audited, alerted and rate limited; admin reads are gated."""

import pytest

from tests.e2e.journeys.harness import PASSWORD, require_path_containing

pytestmark = pytest.mark.e2e

WRONG = "Wr0ng!passw0rd"
ATTEMPTS = 8


@pytest.fixture
async def burst(client, db, new_account):
    """Eight bad logins against one account; returns the codes and audit counts."""
    victim = await new_account("victim")
    audit_before = await db.fetchval(
        "SELECT count(*) FROM audit_logs WHERE event_type = 'login_failed'"
    )
    codes = [
        (
            await client.post(
                "/api/v1/auth/login", json={"email": victim.email, "password": WRONG}
            )
        ).status_code
        for _ in range(ATTEMPTS)
    ]
    return victim, codes, audit_before


async def test_failed_logins_are_audited_and_the_account_is_locked_out(
    client, db, burst
):
    victim, codes, audit_before = burst
    assert codes[0] == 401
    assert codes[-1] == 429, codes

    audited = await db.fetchval(
        "SELECT count(*) FROM audit_logs WHERE event_type = 'login_failed'"
    )
    assert audited - audit_before >= 5

    locked = await client.post(
        "/api/v1/auth/login", json={"email": victim.email, "password": PASSWORD}
    )
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P07-E: failed-login burst raises no security alert",
)
async def test_failed_login_burst_raises_an_alert(db, burst):
    victim, _, _ = burst
    alerts = await db.fetchval(
        "SELECT count(*) FROM security_alerts WHERE user_id = $1::uuid", victim.user_id
    )
    assert alerts >= 1, alerts


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P07-E: RATE_LIMIT_EXCEEDED audit events are not emitted",
)
async def test_rate_limit_hit_is_audited(db, burst):
    _, codes, _ = burst
    assert 429 in codes
    limited = await db.fetchval(
        "SELECT count(*) FROM audit_logs WHERE event_type = 'rate_limit_exceeded'"
    )
    assert limited >= 1, limited


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P07-E: no superuser audit/alert read or ack routes",
)
async def test_superuser_can_read_and_ack_alerts_but_a_normal_user_gets_403(
    client, new_account
):
    await require_path_containing(client, await new_account("alice"), "alerts")
    pytest.fail("routes landed: superuser reads and acks, a normal user gets 403")
