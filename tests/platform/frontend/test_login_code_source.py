"""The CLI-assisted sign-in link's code handling, run under node's test runner."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.support.paths import REPOSITORY_ROOT

TEST_FILE = "tests/platform/frontend/login-code.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_login_code_is_taken_out_of_the_address() -> None:
    # A path relative to the repository: node's ESM loader rejects an absolute Windows path.
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--test", TEST_FILE],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_sign_in_redeems_the_code_before_anything_else() -> None:
    provider = (REPOSITORY_ROOT / "frontend/src/providers/auth-provider.tsx").read_text(
        encoding="utf-8"
    )
    redeem = provider.index("authApi.redeemLoginCode(code)")
    assert (
        provider.index("window.history.replaceState")
        < redeem
        < provider.index("await initialiseMsal()\n    const redirect")
    )
