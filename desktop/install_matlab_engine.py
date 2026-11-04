"""Install MATLAB Engine for Python into the build venv.

MathWorks' setup.py locates MATLAB relative to its own path and writes into the
source tree, which fails under Program Files. This mirrors the layout in a temp
folder (junctions to MATLAB's bin dirs) and pip-installs from there.

Usage: python install_matlab_engine.py [MATLAB_ROOT]
Defaults to PENDULUM_MATLAB_ROOT, then the newest MATLAB under Program Files.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    if os.environ.get("PENDULUM_MATLAB_ROOT"):
        return Path(os.environ["PENDULUM_MATLAB_ROOT"])
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MATLAB"
    candidates = sorted(
        (p for p in base.glob("R*") if (p / "extern" / "engines" / "python").is_dir()),
        key=lambda p: p.name, reverse=True,
    )
    if not candidates:
        raise SystemExit("No MATLAB installation found; pass the MATLAB root as an argument.")
    return candidates[0]


def main() -> int:
    root = find_root()
    source = root / "extern" / "engines" / "python"
    if not source.is_dir():
        raise SystemExit("Not a MATLAB root: " + str(root))
    with tempfile.TemporaryDirectory() as tmp:
        mirror = Path(tmp) / "root"
        (mirror / "extern" / "engines").mkdir(parents=True)
        target = mirror / "extern" / "engines" / "python"
        shutil.copytree(source, target)
        for rel in (Path("bin"), Path("extern") / "bin"):
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(mirror / rel), str(root / rel)],
                check=True, stdout=subprocess.DEVNULL,
            )
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", str(target)], check=True)
        finally:
            for rel in (Path("bin"), Path("extern") / "bin"):
                os.rmdir(mirror / rel)
    print("Installed MATLAB Engine from " + str(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
