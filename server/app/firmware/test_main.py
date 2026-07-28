"""The deploy CLI, exercised without touching hardware or the real sketch dir."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.firmware.__main__ import main


@pytest.fixture
def sketch_dir(tmp_path: Path) -> Path:
    # arduino-cli requires the folder name to match the .ino basename, so the
    # fake sketch dir mirrors the real one.
    directory = tmp_path / "kidink"
    directory.mkdir()
    return directory


@pytest.fixture(autouse=True)
def _device_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply the values with no defaults (conftest seeds the rest)."""
    monkeypatch.setenv("KIDINK_DEVICE_WIFI_SSID", "Test Network")
    monkeypatch.setenv("KIDINK_DEVICE_WIFI_PASSWORD", "hunter2")
    monkeypatch.setenv("KIDINK_DEVICE_SERVER_BASE_URL", "kidink.local:5051")
    get_settings.cache_clear()


def test_no_compile_writes_the_header(
    sketch_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--no-compile", "--sketch-dir", str(sketch_dir)]) == 0
    header = (sketch_dir / "config.h").read_text()
    assert "#define KIDINK_FETCH_URL" in header
    assert "http://kidink.local:5051/display" in header
    out = capsys.readouterr().out
    assert "http://kidink.local:5051/display" in out


def test_clock_sync_schedule_is_printed(
    sketch_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sync wake is invisible on the panel, so the preview is the one place
    a mis-set device_clock_sync_time shows up before the board is on the wall."""
    main(["--no-compile", "--sketch-dir", str(sketch_dir)])
    out = capsys.readouterr().out
    assert "daily at 03:15 from http://kidink.local:5051/time" in out


def test_missing_server_url_exits_before_writing(
    sketch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIDINK_DEVICE_SERVER_BASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="device_server_base_url"):
        main(["--no-compile", "--sketch-dir", str(sketch_dir)])
    assert not (sketch_dir / "config.h").exists()


def test_schedule_preview_lists_the_next_wakes(
    sketch_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preview is what catches a mistyped schedule before it reaches a wall."""
    main(["--no-compile", "--sketch-dir", str(sketch_dir), "--next-fires", "3"])
    lines = capsys.readouterr().out.splitlines()
    preview = [line for line in lines if line.startswith("  ")]
    assert len(preview) == 3
    # The default fires on the hour, every two hours.
    assert all(line.endswith(":00") for line in preview)


def test_cron_override_changes_the_preview(
    sketch_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "--no-compile",
            "--sketch-dir",
            str(sketch_dir),
            "--cron",
            "*/30 * * * *",
            "--next-fires",
            "2",
        ]
    )
    out = capsys.readouterr().out
    assert "'*/30 * * * *'" in out
    assert (sketch_dir / "config.h").read_text().count("*/30 * * * *") == 1


def test_print_config_redacts_and_writes_nothing(
    sketch_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--print-config", "--sketch-dir", str(sketch_dir)]) == 0
    out = capsys.readouterr().out
    assert "<redacted>" in out
    assert "hunter2" not in out
    assert not (sketch_dir / "config.h").exists()


def test_missing_ssid_exits_without_leaking_secrets(
    sketch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIDINK_DEVICE_WIFI_SSID", "")
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as excinfo:
        main(["--no-compile", "--sketch-dir", str(sketch_dir)])
    message = str(excinfo.value)
    assert "KIDINK_DEVICE_WIFI_SSID" in message
    assert "hunter2" not in message
    assert not (sketch_dir / "config.h").exists()


def test_bad_cron_exits_before_writing(
    sketch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="device_wake_cron"):
        main(["--no-compile", "--sketch-dir", str(sketch_dir), "--cron", "nope"])
    assert not (sketch_dir / "config.h").exists()


def test_missing_sketch_dir_is_reported(tmp_path: Path) -> None:
    absent = tmp_path / "not-there"
    with pytest.raises(SystemExit, match="does not exist"):
        main(["--no-compile", "--sketch-dir", str(absent)])


def test_url_override_is_used(sketch_dir: Path) -> None:
    main(
        [
            "--no-compile",
            "--sketch-dir",
            str(sketch_dir),
            "--url",
            "http://pi.local:5051/display",
        ]
    )
    assert "http://pi.local:5051/display" in (sketch_dir / "config.h").read_text()
