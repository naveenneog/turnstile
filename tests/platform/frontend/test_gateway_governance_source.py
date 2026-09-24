"""The gateway governance page's editing rules, run under node's test runner."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.support.paths import REPOSITORY_ROOT

TEST_FILE = "tests/platform/frontend/gateway-governance-model.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_gateway_governance_editing_rules() -> None:
    # A path relative to the repository: node's ESM loader rejects an absolute Windows path.
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--test", TEST_FILE],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
