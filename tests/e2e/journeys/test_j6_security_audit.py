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


async def test_failed_login_burst_raises_one_alert(db, burst):
    victim, _, _ = burst
    alerts = await db.fetchval(
        "SELECT count(*) FROM security_alerts "
        "WHERE user_id = $1::uuid AND alert_type = 'failed_login_attempts'",
        victim.user_id,
    )
    assert alerts == 1, alerts


async def test_rate_limit_hit_is_audited(db, burst):
    _, codes, _ = burst
    assert 429 in codes
    limited = await db.fetchval(
        "SELECT count(*) FROM audit_logs WHERE event_type = 'rate_limit_exceeded'"
    )
    assert limited >= 1, limited


ADMIN_READS = ["/api/v1/admin/audit-events", "/api/v1/admin/alerts"]


async def test_admin_reads_are_superuser_only(client, db, burst, new_account):
    alice = await new_account("alice")
    for path in ADMIN_READS:
        denied = await client.get(path, headers=alice.headers)
        assert denied.status_code == 403, (path, denied.status_code)

    await db.execute(
        "UPDATE users SET is_superuser = true WHERE id = $1::uuid", alice.user_id
    )
    victim, _, _ = burst
    alerts = await client.get(
        "/api/v1/admin/alerts",
        headers=alice.headers,
        params={"alert_type": "failed_login_attempts"},
    )
    assert alerts.status_code == 200, alerts.text
    assert victim.user_id in {a["user_id"] for a in alerts.json()["items"]}
    events = await client.get(ADMIN_READS[0], headers=alice.headers)
    assert events.status_code == 200, events.text
    assert events.json()["items"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P07-E wave 2: no security alert acknowledge route",
)
async def test_superuser_can_ack_alerts(client, new_account):
    await require_path_containing(client, await new_account("alice"), "/ack")
    pytest.fail("route landed: superuser acks an alert, a normal user gets 403")
