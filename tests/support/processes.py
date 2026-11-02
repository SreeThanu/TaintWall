"""Run a helper process that prints `READY <url>` once it is listening; stop it cleanly."""

from __future__ import annotations

import queue
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
READY_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 5.0


@dataclass
class ReadyProcess:
    url: str
    process: subprocess.Popen[str]
    stderr_path: Path

    def stderr_text(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> int:
        """Terminate (then kill if needed); return the exit code."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=STOP_TIMEOUT_S)
        return self.process.returncode


@contextmanager
def ready_process(args: list[str]) -> Iterator[ReadyProcess]:
    """Start `python <args>` from the repo root and wait for its `READY <url>` line."""
    stderr_file = tempfile.NamedTemporaryFile(  # noqa: SIM115  # closed in finally
        prefix="taintwall-proc-", suffix=".log", delete=False
    )
    process = subprocess.Popen(  # noqa: S603  # fixed argv, our own interpreter and module
        [sys.executable, *args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        text=True,
    )
    lines: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_stdout, name="ready-process-stdout", daemon=True).start()
    running: ReadyProcess | None = None
    try:
        try:
            line = lines.get(timeout=READY_TIMEOUT_S)
        except queue.Empty:
            raise RuntimeError(f"{args!r}: no READY line within {READY_TIMEOUT_S}s") from None
        if not line.startswith("READY "):
            raise RuntimeError(f"{args!r}: unexpected first line {line[:80]!r}")
        running = ReadyProcess(line.split(" ", 1)[1].strip(), process, Path(stderr_file.name))
        yield running
    finally:
        if running is None:
            running = ReadyProcess("", process, Path(stderr_file.name))
        code = running.stop()
        stderr_file.close()
        if process.poll() is None:
            raise RuntimeError(f"{args!r}: process did not exit (code {code})")


@contextmanager
def upstream_subprocess() -> Iterator[ReadyProcess]:
    with ready_process(["-m", "tests.support.upstream", "--serve"]) as running:
        yield running
