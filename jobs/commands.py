"""Running the srnatoolbox command-line tools (sRNAbench, sRNAhelper, sRNAde) from job runners."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from django.conf import settings


def srnatoolbox_command(tool: str) -> tuple[str, dict]:
    """Path of a srnatoolbox command in the conda env, and an environment with the env's bin on PATH."""
    bin_dir = Path(settings.SRNATOOLBOX_ENV) / "bin"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return str(bin_dir / tool), env


def run_command(cmd, directory: Path, env, log, logname: str, timeout_h: float) -> None:
    """Run a command in the job directory; output goes to `logname`, a short tail into the error on failure."""
    log("running: " + " ".join([Path(cmd[0]).name] + [str(c) for c in cmd[1:]]))
    with open(directory / logname, "a") as out:
        proc = subprocess.run([str(c) for c in cmd], stdout=out, stderr=subprocess.STDOUT, env=env, cwd=directory,
                              timeout=timeout_h * 3600)
    if proc.returncode != 0:
        tail = (directory / logname).read_text(errors="replace")[-2000:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed (exit {proc.returncode}):\n{tail}")
