from pathlib import Path

import pytest

from app.eink import arduino


def test_compile_cmd() -> None:
    cmd = arduino.compile_cmd(Path("/repo/arduino/mockup"), Path("/tmp/build"))
    assert cmd == [
        "arduino-cli",
        "compile",
        "--fqbn",
        "soldered-inkplate-boards:esp32:Inkplate13SPECTRA",
        "--build-path",
        "/tmp/build",
        "/repo/arduino/mockup",
    ]


def test_upload_cmd() -> None:
    cmd = arduino.upload_cmd(
        Path("/repo/arduino/mockup"), Path("/tmp/build"), "/dev/cu.wchusbserial110"
    )
    assert cmd == [
        "arduino-cli",
        "upload",
        "--fqbn",
        "soldered-inkplate-boards:esp32:Inkplate13SPECTRA",
        "-p",
        "/dev/cu.wchusbserial110",
        "--input-dir",
        "/tmp/build",
        "/repo/arduino/mockup",
    ]


def test_resolve_port_returns_existing_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arduino.Path, "exists", lambda self: True)
    assert arduino.resolve_port("/dev/cu.wchusbserial110") == "/dev/cu.wchusbserial110"


def test_resolve_port_falls_back_to_single_glob_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arduino.Path, "exists", lambda self: False)
    monkeypatch.setattr(
        arduino.glob, "glob", lambda pattern: ["/dev/cu.wchusbserial99"]
    )
    assert arduino.resolve_port("/dev/cu.wchusbserial110") == "/dev/cu.wchusbserial99"


def test_resolve_port_errors_when_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arduino.Path, "exists", lambda self: False)
    monkeypatch.setattr(arduino.glob, "glob", lambda pattern: [])
    with pytest.raises(SystemExit, match="plugged in"):
        arduino.resolve_port("/dev/cu.wchusbserial110")


def test_resolve_port_errors_on_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arduino.Path, "exists", lambda self: False)
    monkeypatch.setattr(
        arduino.glob,
        "glob",
        lambda pattern: ["/dev/cu.wchusbserial1", "/dev/cu.wchusbserial2"],
    )
    with pytest.raises(SystemExit, match="multiple"):
        arduino.resolve_port("/dev/cu.wchusbserial110")
