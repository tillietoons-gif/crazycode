"""Background job manager: run shell commands without blocking the agent loop.

Jobs are plain ``subprocess.Popen`` processes whose combined stdout/stderr is
streamed to a file under ``.pycode/jobs/``. Tools can start a job, poll its
output, list it, or kill it - so the LLM can run dev servers, long builds, or
installers and keep working while they run.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess  # noqa: S404 - commands come from the user/LLM with confirmation
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class JobManager:
    """Tracks background shell processes started by the agent."""

    def __init__(self, output_dir: Optional[str] = None, max_output_bytes: int = 200_000):
        self.output_dir = Path(output_dir or os.path.join(tempfile.gettempdir(), "pycode-jobs"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_output_bytes = max_output_bytes
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._next_id = 1

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def start(self, command: str, workdir: Optional[str] = None) -> Dict[str, Any]:
        """Start ``command`` in the background; returns a job descriptor."""
        job_id = f"job-{self._next_id}"
        self._next_id += 1
        out_path = self.output_dir / f"{job_id}.log"
        try:
            with open(out_path, "wb") as fh:
                # shell=True: /bin/sh on POSIX, cmd.exe on Windows
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=workdir or None,
                    start_new_session=(os.name == "posix"),
                )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"failed to start job: {exc}", "ok": False}

        self._jobs[job_id] = {
            "id": job_id,
            "command": command,
            "workdir": workdir or os.getcwd(),
            "proc": proc,
            "output_path": str(out_path),
            "started": time.time(),
        }
        return {"ok": True, "job_id": job_id, "command": command, "output_path": str(out_path)}

    def _entry_status(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        proc = entry["proc"]
        code = proc.poll()
        return {
            "running": code is None,
            "exit_code": code,
            "elapsed_s": round(time.time() - entry["started"], 1),
        }

    def status(self, job_id: str) -> Dict[str, Any]:
        entry = self._jobs.get(job_id)
        if entry is None:
            return {"error": f"unknown job: {job_id}"}
        out = {"job_id": job_id, "command": entry["command"], **self._entry_status(entry)}
        return out

    def output(self, job_id: str, tail: int = 60) -> Dict[str, Any]:
        """Return job status plus the last ``tail`` lines of output."""
        entry = self._jobs.get(job_id)
        if entry is None:
            return {"error": f"unknown job: {job_id}"}
        out = self.status(job_id)
        try:
            data = Path(entry["output_path"]).read_bytes()[-self.max_output_bytes:]
            lines = data.decode("utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        out["output"] = "\n".join(lines[-tail:])
        out["truncated"] = len(lines) > tail
        return out

    def list(self) -> List[Dict[str, Any]]:
        return [self.status(job_id) for job_id in self._jobs]

    def kill(self, job_id: str) -> Dict[str, Any]:
        entry = self._jobs.get(job_id)
        if entry is None:
            return {"error": f"unknown job: {job_id}"}
        proc = entry["proc"]
        if proc.poll() is None:
            try:
                if os.name == "posix":
                    # kill the whole process group so shell children die too
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            except (ProcessLookupError, PermissionError):
                pass
        # reap regardless of who finished first; wait so OS handles release
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return {"ok": True, "job_id": job_id, "killed": True}

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def reap(self) -> int:
        """Drop finished jobs from memory (their logs stay on disk)."""
        done = [jid for jid, e in self._jobs.items() if e["proc"].poll() is not None]
        for jid in done:
            del self._jobs[jid]
        return len(done)

    def tool_payload(self) -> str:
        """JSON summary suitable for feeding back to the LLM."""
        return json.dumps({"jobs": self.list()}, ensure_ascii=False, default=str)
