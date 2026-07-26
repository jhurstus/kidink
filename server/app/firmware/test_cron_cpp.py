"""Conformance: the device's C++ cron must agree with the Python reference.

`arduino/kidink/cron.cpp` is what actually decides when the panel wakes, but it
only ever runs on an ESP32 - so it is compiled here for the host and driven with
the very same table (`cron_cases.py`) that `test_cron.py` checks the Python
implementation against. A divergence between the two shows up as a failing test
rather than as a board that wakes at the wrong hour.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.firmware.cron_cases import CASES, INVALID

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _REPO_ROOT / "arduino" / "kidink_tests" / "cron_host_test.cpp"

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="no host C++ compiler available"
)


@pytest.fixture(scope="module")
def cron_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the host harness once for the whole module."""
    binary = tmp_path_factory.mktemp("cron") / "cron_host_test"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(binary),
            str(_HARNESS),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def _run(binary: Path, rows: list[tuple[str, str, str]]) -> list[str]:
    stdin = "".join(f"{expr}\t{after}\t{expected}\n" for expr, after, expected in rows)
    result = subprocess.run(
        [str(binary)], input=stdin, capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines()


def test_harness_compiles(cron_binary: Path) -> None:
    """The sketch's cron unit builds clean with warnings as errors."""
    assert cron_binary.is_file()


def test_next_fire_matches_reference(cron_binary: Path) -> None:
    rows = [
        (expr, after, expected if expected is not None else "NONE")
        for expr, after, expected in CASES
    ]
    lines = _run(cron_binary, rows)
    assert len(lines) == len(rows)
    failures = [line for line in lines if not line.startswith("OK")]
    assert not failures, "C++ cron diverges from the Python reference:\n" + "\n".join(
        failures
    )


def test_invalid_expressions_rejected(cron_binary: Path) -> None:
    rows = [(expr, "2026-01-01T00:00:00", "INVALID") for expr, _ in INVALID]
    lines = _run(cron_binary, rows)
    assert len(lines) == len(rows)
    failures = [line for line in lines if not line.startswith("OK")]
    assert not failures, "C++ cron accepted an invalid expression:\n" + "\n".join(
        failures
    )
