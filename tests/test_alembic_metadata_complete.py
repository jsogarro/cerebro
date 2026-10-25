"""alembic/env.py must see every table, or autogenerate emits drop_table for the rest."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_model_imports() -> str:
    """The `src.models` import statements alembic/env.py executes."""
    tree = ast.parse((REPO_ROOT / "alembic" / "env.py").read_text())
    return "\n".join(
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        and "src.models" in ast.unparse(node)
    )


def test_every_model_table_is_in_metadata_after_env_imports() -> None:
    declared = {
        match
        for path in (REPO_ROOT / "src" / "models").rglob("*.py")
        for match in re.findall(r'__tablename__\s*=\s*"(\w+)"', path.read_text())
    }
    assert declared, "no __tablename__ found under src/models"

    # Fresh interpreter: only what env.py imports may populate the metadata.
    script = (
        f"{_env_model_imports()}\n"
        "import json\n"
        "from src.models.db.base import Base\n"
        "print(json.dumps(sorted(Base.metadata.tables)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    registered = set(json.loads(result.stdout.splitlines()[-1]))

    assert declared - registered == set()
