from __future__ import annotations

import json
import os
import resource
import threading
import time
from pathlib import Path


class Monitor:
    def __init__(self, path: Path, period=0.05):
        self.path = path
        self.period = period
        self.rows = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )

    def _rss(self):
        try:
            pages = int(
                Path("/proc/self/statm").read_text().split()[1]
            )
            return pages * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            return 0

    def _run(self):
        while not self.stop_event.wait(self.period):
            self.rows.append(
                {
                    "timestamp_ns": time.time_ns(),
                    "rss_bytes": self._rss(),
                }
            )

        self.rows.append(
            {
                "timestamp_ns": time.time_ns(),
                "rss_bytes": self._rss(),
            }
        )

    def __enter__(self):
        self.thread.start()
        self.cpu_start = time.process_time_ns()
        self.wall_start = time.perf_counter_ns()
        return self

    def __exit__(self, *exc):
        self.cpu_ns = time.process_time_ns() - self.cpu_start
        self.wall_ns = time.perf_counter_ns() - self.wall_start
        self.stop_event.set()
        self.thread.join()

        peak = (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * 1024
        )

        with self.path.open("x", encoding="utf-8") as f:
            json.dump(
                {
                    "sampled": self.rows,
                    "os_peak_rss_bytes": peak,
                    "process_cpu_ns": self.cpu_ns,
                    "wall_ns": self.wall_ns,
                },
                f,
                separators=(",", ":"),
            )
            f.write("\n")