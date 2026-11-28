import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "mint_metrics_token.py"


def _run(key_path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    # The documented invocation: run by path from any directory.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["JWT_PRIVATE_KEY_PATH"] = str(key_path)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_missing_key_fails_without_generating_one(tmp_path: Path) -> None:
    key = tmp_path / "absent" / "jwt_private.pem"

    result = _run(key, tmp_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert not key.exists() and not key.parent.exists()


def test_existing_key_mints_a_token_signed_by_it(tmp_path: Path) -> None:
    key = tmp_path / "jwt_private.pem"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    result = _run(key, tmp_path)

    assert result.returncode == 0, result.stderr
    from jose import jwt

    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(
        result.stdout.strip(), public, algorithms=["RS256"], audience="cerebro-metrics"
    )
    assert claims["permissions"] == ["metrics:read"]
