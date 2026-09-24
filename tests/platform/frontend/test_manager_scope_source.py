from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.support.paths import REPOSITORY_ROOT


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_manager_scope_model_rules() -> None:
    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            "tests/platform/frontend/manager-scope.test.mjs",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
