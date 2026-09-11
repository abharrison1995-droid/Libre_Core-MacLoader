"""Run an installed wheel or sdist outside the source checkout."""

from __future__ import annotations

import argparse
import os
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
        child_env = os.environ.copy()
        if python.exists():
            subprocess.check_call([str(python), "-m", "pip", "install", str(artifact)])
            python_cmd = [str(python)]
            console_cmd = [str(venv_dir / ("Scripts/macloader.exe" if sys.platform == "win32" else "bin/macloader"))]
        else:
            # Some minimal Linux images omit python3-venv.  Keep the clean
            # checkout/package boundary by installing into a temporary target
            # directory rather than falling back to the source checkout.
            isolated_target = temp / "installed-target"
            isolated_target.mkdir()
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(isolated_target), str(artifact)])
            child_env["PYTHONPATH"] = str(isolated_target) + os.pathsep + child_env.get("PYTHONPATH", "")
            python_cmd = [sys.executable]
            console_cmd = [sys.executable, "-m", "macloader"]

        def check_call(command: list[str]) -> None:
            subprocess.check_call(command, cwd=run_dir, env=child_env)

        def check_output(command: list[str]) -> str:
            return subprocess.check_output(command, cwd=run_dir, env=child_env, text=True)

        check_call([*console_cmd, "--help"])
        check_call([*python_cmd, "-m", "macloader", "--help"])
        check_call([*console_cmd, "build", "--help"])
        check_call([*console_cmd, "validate", "--help"])
        recovery_policy = check_output([*console_cmd, "recovery", "list", "--json"])
        if '"version": "15.0"' not in recovery_policy or '"build": "24A335"' not in recovery_policy:
            raise AssertionError("installed recovery policy did not preserve the exact target")
        check_call(
            [
                *python_cmd,
                "-c",
                "from macloader.recovery.service import load_recovery_policy; assert load_recovery_policy().target.build == '24A335'; import macloader.build, macloader.domain.contracts, macloader.domain.recovery, macloader.recovery, macloader.removable",
            ],
        )
        tui_smoke = """
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from macloader.configuration.store import ConfigurationStore
from macloader.domain.configuration import UserConfiguration
from macloader.ui.tui import WorkflowApp
from macloader.workflow.service import WorkflowService

async def main():
    service = WorkflowService(store=ConfigurationStore(Path.cwd() / 'tui-configs'))
    app = WorkflowApp(fixture=Path(sys.argv[1]), service=service)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one('#workflow-status')
        assert app.query_one('#workflow-stages')
        app._apply_target()
        state = service.evaluate(app._draft, app._snapshot)
        cli_payload = json.loads(subprocess.check_output([
            sys.executable, '-m', 'macloader', 'configure', '--version', '15.0', '--build', '24A335',
            '--fixture', sys.argv[1], '--json'
        ], text=True))
        cli_configuration = UserConfiguration.from_dict(cli_payload['configuration'])
        assert state.configuration.semantic_digest == cli_configuration.semantic_digest
        assert state.configuration.target.to_dict() == cli_payload['configuration']['target']
        assert [item.code for item in state.evaluation.issues] == [item['code'] for item in cli_payload['issues']]
        assert state.evaluation.plan.target_build == cli_payload['plan']['target_build']
        assert state.evaluation.plan.unresolved_requirements == cli_payload['plan']['unresolved_requirements']

asyncio.run(main())
"""
        check_call([*python_cmd, "-c", tui_smoke, str(fixture)])

        probe = check_output([*console_cmd, "probe", "--fixture", str(fixture), "--json"])
        if '"machine_type": "20L7"' not in probe:
            raise AssertionError("installed probe did not report the fixture machine type")
        resolved = check_output([*console_cmd, "deps", "resolve", "--fixture", str(fixture), "--macos", "sequoia", "--json"])
        if '"catalog_digest"' not in resolved:
            raise AssertionError("installed resolver did not emit a catalog digest")


if __name__ == "__main__":
    main()
