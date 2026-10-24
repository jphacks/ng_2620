"""Runs a pipeline script as a child process in its own process group (ADR-0014 §3).

stdout and stderr go, merged, to the job's log file and line by line to on_line. tqdm's carriage-return updates are
split into lines too, so progress bars can be parsed."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Callable


class SubprocessPipelineRunner:
    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None

    async def run(self, cmd: list[str], env: dict, cwd: Path, on_line: Callable[[str], None], log_path: Path) -> int:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000200          # CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        log_path.parent.mkdir(parents=True, exist_ok=True)
        full_env = {**os.environ, **env, "PYTHONUNBUFFERED": "1"}
        with open(log_path, "ab") as log:
            log.write(("$ " + " ".join(map(str, cmd)) + "\n").encode())
            self._proc = await asyncio.create_subprocess_exec(*map(str, cmd), cwd=str(cwd), env=full_env,
                                                              stdout=asyncio.subprocess.PIPE,
                                                              stderr=asyncio.subprocess.STDOUT, **kw)
            buf = b""
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                buf += chunk
                *lines, buf = buf.replace(b"\r", b"\n").split(b"\n")
                for ln in lines:
                    if ln.strip():
                        on_line(ln.decode("utf-8", "replace"))
            if buf.strip():
                on_line(buf.decode("utf-8", "replace"))
            return await self._proc.wait()

    async def terminate(self) -> None:
        p = self._proc
        if p is None or p.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                os.kill(p.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(p.wait(), timeout=10)
        except asyncio.TimeoutError:
            if sys.platform == "win32":
                p.kill()
            else:
                os.killpg(p.pid, signal.SIGKILL)
            await p.wait()
