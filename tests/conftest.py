import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))


# SEC-18 (11 Nov): the UI tests skip when node + jsdom are not installed, which is right on a bare machine but wrong inside
# `make check`, where a skipped UI test looked like a pass. With TMKG_REQUIRE_JSDOM=1 (set by `make check` / `make tests`,
# which install jsdom first) a test skipped for lack of jsdom FAILS instead. Other skips (torch, zstd, ...) are untouched.
import os

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("TMKG_REQUIRE_JSDOM") == "1" and report.skipped and "jsdom" in str(report.longrepr).lower():
        report.outcome = "failed"
        report.longrepr = ("jsdom is required here (TMKG_REQUIRE_JSDOM=1), but this UI test was about to be skipped: "
                           f"{str(report.longrepr)[-200:]}. Install it with `make jsdom` (needs node and npm).")
