"""arduino-cli command construction and execution for the demo sketch."""

import glob
import subprocess
from pathlib import Path

FQBN = "soldered-inkplate-boards:esp32:Inkplate13SPECTRA"
DEFAULT_PORT = "/dev/cu.wchusbserial110"
_PORT_GLOB = "/dev/cu.wchusbserial*"


def compile_cmd(sketch_dir: Path, build_dir: Path, fqbn: str = FQBN) -> list[str]:
    return [
        "arduino-cli",
        "compile",
        "--fqbn",
        fqbn,
        "--build-path",
        str(build_dir),
        str(sketch_dir),
    ]


def upload_cmd(
    sketch_dir: Path, build_dir: Path, port: str, fqbn: str = FQBN
) -> list[str]:
    return [
        "arduino-cli",
        "upload",
        "--fqbn",
        fqbn,
        "-p",
        port,
        "--input-dir",
        str(build_dir),
        str(sketch_dir),
    ]


def resolve_port(requested: str) -> str:
    """Return `requested` if present, else fall back to the single glob match."""
    if Path(requested).exists():
        return requested
    candidates = sorted(glob.glob(_PORT_GLOB))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(
            f"No serial port at {requested} and no {_PORT_GLOB} device found. "
            "Is the Inkplate plugged in via USB?"
        )
    raise SystemExit(
        f"No serial port at {requested}; multiple candidates found: "
        f"{', '.join(candidates)}. Pick one with --port."
    )


def run_checked(cmd: list[str]) -> None:
    """Run a command with inherited stdio (live progress), failing loudly."""
    print(f"$ {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"{cmd[0]} not found — install it (brew install arduino-cli) first."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"{cmd[0]} {cmd[1]} failed with exit code {exc.returncode}"
        ) from exc
