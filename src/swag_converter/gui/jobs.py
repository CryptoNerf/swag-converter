"""Running conversions without freezing the window.

Every conversion is its own process.  That is not about throughput — the
tracer is mostly NumPy and would thread tolerably — but about cancellation:
a user who dropped a 4K photograph by mistake wants it to stop now, and the
only reliable way to stop a tight numeric loop is to kill the process running
it.  It also keeps a segfault in a native dependency from taking the window
down with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import multiprocessing as mp
import os
from pathlib import Path
import queue as queuelib
import threading
import time
from typing import Any, Callable
import uuid


#: Stages the tracer reports, in the order they happen, so the interface can
#: show a bar that means something rather than a spinner that does not.
STAGES = ("reading", "analysing", "denoising", "segmenting", "merging regions",
          "fitting curves", "scoring")


@dataclass
class Job:
    identifier: str
    source: Path
    destination: Path
    status: str = "queued"          # queued | running | done | failed | cancelled
    stage: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None
    started: float | None = None
    finished: float | None = None
    #: Dropped files are copied into a directory we own and delete on exit.
    temporary: bool = False
    #: What produced ``result``.  A finished job whose settings no longer
    #: match the ones on screen is out of date, not done.
    settings_used: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self.source.name

    def outdated(self, options: dict[str, Any]) -> bool:
        """Would converting it again produce something different?"""
        return self.status != "done" or self.settings_used != options

    def state(self) -> dict[str, Any]:
        elapsed = None
        if self.started is not None:
            elapsed = round((self.finished or time.monotonic()) - self.started, 1)
        return {
            "id": self.identifier,
            "name": self.name,
            "source": str(self.source),
            "destination": str(self.destination),
            "status": self.status,
            "stage": self.stage,
            "progress": (STAGES.index(self.stage) + 1) / len(STAGES) if self.stage in STAGES else 0,
            "error": self.error,
            "result": self.result,
            "elapsed": elapsed,
        }


def _convert_in_process(
    identifier: str, source: str, destination: str, options: dict[str, Any], channel: Any
) -> None:
    """Worker entry point.  Runs in its own process; talks back down ``channel``."""
    try:
        from ..convert import convert

        def step(label: str) -> None:
            channel.put(("stage", identifier, label))

        result = convert(Path(source), Path(destination), progress=step, **options)
        channel.put((
            "done",
            identifier,
            {
                "destination": str(result.destination),
                "preset": result.preset,
                "quality": result.quality,
                "content": result.analysis.kind,
                "regions": result.regions,
                "gradients": result.gradients,
                "nodes": result.nodes,
                "svg_bytes": result.svg_bytes,
                "source_bytes": result.source_bytes,
                "width": result.width,
                "height": result.height,
                "source_size": list(result.source_size),
                "similarity": result.similarity,
                "seconds": round(result.seconds, 2),
                "warnings": result.warnings,
            },
        ))
    except Exception as exc:  # a bad file must not take the queue down
        channel.put(("failed", identifier, f"{type(exc).__name__}: {exc}"))


class Queue:
    """A batch of conversions, one process at a time per slot."""

    def __init__(self, workers: int | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._pending: list[str] = []
        self._running: dict[str, mp.Process] = {}
        self._context = mp.get_context("spawn")
        self._channel: Any = None
        self._options: dict[str, Any] = {}
        self._signature: dict[str, Any] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))

    # -- queue contents ----------------------------------------------------

    def add(self, source: Path, destination: Path, temporary: bool = False) -> Job:
        job = Job(identifier=uuid.uuid4().hex[:12], source=source,
                  destination=destination, temporary=temporary)
        with self._lock:
            self._jobs[job.identifier] = job
            self._order.append(job.identifier)
        return job

    def remove(self, identifier: str) -> None:
        with self._lock:
            job = self._jobs.get(identifier)
            if job is None or job.status == "running":
                return
            self._order.remove(identifier)
            del self._jobs[identifier]
            if identifier in self._pending:
                self._pending.remove(identifier)
        if job.temporary:
            _discard(job.source)

    def clear(self, finished_only: bool = False) -> None:
        with self._lock:
            targets = [
                identifier for identifier in list(self._order)
                if self._jobs[identifier].status != "running"
                and (not finished_only or self._jobs[identifier].status in ("done", "failed", "cancelled"))
            ]
        for identifier in targets:
            self.remove(identifier)

    def jobs(self) -> list[Job]:
        with self._lock:
            return [self._jobs[identifier] for identifier in self._order]

    def outdated(self, options: dict[str, Any]) -> int:
        """How many jobs pressing Convert would actually run."""
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.outdated(options))

    def rerun_all(self) -> None:
        """Forget what was converted, so everything runs again."""
        with self._lock:
            for job in self._jobs.values():
                if job.status != "running":
                    job.settings_used = None

    def job(self, identifier: str) -> Job | None:
        with self._lock:
            return self._jobs.get(identifier)

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._running or self._pending)

    # -- running -----------------------------------------------------------

    def start(
        self,
        options: dict[str, Any],
        destination_for: Callable[[Job], Path],
        signature: dict[str, Any] | None = None,
        only: set[str] | None = None,
    ) -> None:
        """``options`` go to the tracer; ``signature`` decides what is stale.

        They differ by the output folder: the tracer is handed a path, not a
        folder, but moving the folder still means a finished job's result is
        in the wrong place.

        ``only`` names the jobs to run.  Given one, it is obeyed exactly —
        a picked image is converted whether or not anything says it needs to
        be, because picking it is the whole statement.  Given none, whatever
        is out of date runs.
        """
        with self._lock:
            if self._running or self._pending:
                return
            self._options = dict(options)
            self._signature = dict(signature if signature is not None else options)
            chosen: set[str] = set()
            for job in self._jobs.values():
                if only is not None:
                    wanted = job.identifier in only
                else:
                    # A finished job is only finished for the settings that
                    # finished it.  Change the detail and press Convert and
                    # the expectation is plainly that it runs again.
                    wanted = job.outdated(self._signature)
                if not wanted:
                    continue
                chosen.add(job.identifier)
                job.destination = destination_for(job)
                job.status = "queued"
                job.stage = ""
                job.error = None
                job.result = None
                job.started = None
                job.finished = None
            # Queue what this run chose, not everything that happens to be
            # sitting at "queued": an image added but never converted is at
            # that status too, and must not ride along on someone else's
            # selection.
            self._pending = [
                identifier for identifier in self._order if identifier in chosen
            ]
            if not self._pending:
                return
            if self._channel is not None:
                try:
                    self._channel.close()
                except Exception:
                    pass
            self._channel = self._context.Queue()
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop everything, killing whatever is mid-conversion.

        Signal every worker before waiting on any of them.  Terminating and
        joining one at a time costs the timeout once per worker, which is how
        closing the window mid-batch came to look like a hang.
        """
        self._stop.set()
        with self._lock:
            self._pending.clear()
            processes = list(self._running.items())
        for _, process in processes:
            if process.is_alive():
                process.terminate()
        for identifier, process in processes:
            process.join(timeout=3)
            if process.is_alive():  # ignored SIGTERM; insist
                process.kill()
                process.join(timeout=1)
            with self._lock:
                job = self._jobs.get(identifier)
                if job is not None and job.status == "running":
                    job.status = "cancelled"
                    job.stage = ""
                    job.finished = time.monotonic()
                self._running.pop(identifier, None)

    def _pump(self) -> None:
        """Keep the slots full and drain whatever the workers report."""
        while not self._stop.is_set():
            with self._lock:
                while self._pending and len(self._running) < self._workers:
                    identifier = self._pending.pop(0)
                    job = self._jobs[identifier]
                    job.status = "running"
                    job.stage = STAGES[0]
                    job.started = time.monotonic()
                    process = self._context.Process(
                        target=_convert_in_process,
                        args=(identifier, str(job.source), str(job.destination),
                              self._options, self._channel),
                        daemon=True,
                    )
                    process.start()
                    self._running[identifier] = process
                idle = not self._running and not self._pending
            if idle:
                return
            try:
                kind, identifier, payload = self._channel.get(timeout=0.2)
            except (queuelib.Empty, OSError):
                self._reap()
                continue
            with self._lock:
                job = self._jobs.get(identifier)
                if job is None:
                    continue
                if kind == "stage":
                    job.stage = payload
                elif kind == "done":
                    job.status, job.result, job.stage = "done", payload, ""
                    job.finished = time.monotonic()
                    job.settings_used = dict(self._signature)
                elif kind == "failed":
                    job.status, job.error, job.stage = "failed", payload, ""
                    job.finished = time.monotonic()
            if kind in ("done", "failed"):
                self._retire(identifier)

    def _retire(self, identifier: str) -> None:
        with self._lock:
            process = self._running.pop(identifier, None)
        if process is not None:
            process.join(timeout=5)

    def _reap(self) -> None:
        """Catch a worker that died without reporting — a segfault, an OOM kill."""
        with self._lock:
            dead = [
                (identifier, process) for identifier, process in self._running.items()
                if not process.is_alive()
            ]
            for identifier, process in dead:
                self._running.pop(identifier, None)
                job = self._jobs.get(identifier)
                if job is not None and job.status == "running":
                    job.status = "failed"
                    job.stage = ""
                    job.finished = time.monotonic()
                    job.error = f"the converter stopped unexpectedly (exit code {process.exitcode})"

    def shutdown(self) -> None:
        """Safe to call twice: the window's close handler and the exit path
        both run it, and on this platform the close handler runs inline."""
        self.cancel()
        for job in self.jobs():
            if job.temporary:
                _discard(job.source)
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
