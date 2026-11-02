"""The latency benchmark's command line, run for real with a very small sample."""

import subprocess
import sys
from pathlib import Path

from tests.support.processes import REPO_ROOT


def test_runs_dir_outside_the_repo_finishes_cleanly(tmp_path: Path) -> None:
    runs_dir = tmp_path / "elsewhere"
    completed = subprocess.run(  # noqa: S603  # fixed argv, our own interpreter and module
        [
            sys.executable,
            "-m",
            "loadtest.passthrough_latency",
            "--n",
            "3",
            "--warmup",
            "0",
            "--runs-dir",
            str(runs_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    [run] = list(runs_dir.iterdir())
    assert (run / "metrics.json").is_file()
    assert (run / "config.yaml").is_file()
    assert completed.returncode == 0, completed.stderr[-500:]
    assert f"saved: {run}" in completed.stdout
