"""Run an installed wheel or sdist outside the source checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    fixture = args.fixture.resolve()
    with tempfile.TemporaryDirectory(prefix="macloader-package-smoke-") as temp_name:
        temp = Path(temp_name)
        venv_dir = temp / "venv"
        run_dir = temp / "run"
        run_dir.mkdir()
        venv.create(venv_dir, with_pip=True)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.check_call([str(python), "-m", "pip", "install", str(artifact)])
        console = venv_dir / ("Scripts/macloader.exe" if sys.platform == "win32" else "bin/macloader")
        subprocess.check_call([str(console), "--help"], cwd=run_dir)
        subprocess.check_call([str(python), "-m", "macloader", "--help"], cwd=run_dir)
        subprocess.check_call([str(console), "build", "--help"], cwd=run_dir)
        subprocess.check_call([str(console), "validate", "--help"], cwd=run_dir)
        subprocess.check_call(
            [
                str(python),
                "-c",
                "import macloader.build, macloader.domain.contracts, macloader.recovery, macloader.removable",
            ],
            cwd=run_dir,
        )

        probe = subprocess.check_output(
            [str(console), "probe", "--fixture", str(fixture), "--json"],
            cwd=run_dir,
            text=True,
        )
        if '"machine_type": "20L7"' not in probe:
            raise AssertionError("installed probe did not report the fixture machine type")
        resolved = subprocess.check_output(
            [str(console), "deps", "resolve", "--fixture", str(fixture), "--macos", "sequoia", "--json"],
            cwd=run_dir,
            text=True,
        )
        if '"catalog_digest"' not in resolved:
            raise AssertionError("installed resolver did not emit a catalog digest")


if __name__ == "__main__":
    main()
