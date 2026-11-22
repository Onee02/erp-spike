"""Month-end simulation: the whale's finance team runs reports all at once.

Workers call the API over HTTP, like real users would, so everything between
the browser and the database applies to them too.
"""
import os
import threading

import httpx

from . import worker

API_URL = os.getenv("SIM_API_URL", "http://127.0.0.1:8000")
WORKERS = int(os.getenv("SIM_WHALE_WORKERS", "40"))
if API_URL.rstrip("/") != "http://127.0.0.1:8000":
    raise RuntimeError("Simulation must target this worker at http://127.0.0.1:8000")
if not 1 <= WORKERS <= 128:
    raise RuntimeError("SIM_WHALE_WORKERS must be between 1 and 128")

_stop = threading.Event()
_threads: list[threading.Thread] = []
_lifecycle_lock = threading.Lock()


def _worker() -> None:
    with httpx.Client(base_url=API_URL, timeout=60) as client:
        while not _stop.is_set():
            try:
                response = client.get("/t/1/reports/monthly", params={"year": 2026})
                response.raise_for_status()
            except httpx.HTTPError:
                _stop.wait(0.5)


def running() -> bool:
    with _lifecycle_lock:
        return any(t.is_alive() for t in _threads)


def status() -> dict:
    worker.require_whale()
    active = running()
    return {"running": active, "stopping": active and _stop.is_set()}


def start() -> None:
    worker.require_whale()
    with _lifecycle_lock:
        if any(t.is_alive() for t in _threads):
            return
        _stop.clear()
        _threads.clear()
        for _ in range(WORKERS):
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            _threads.append(t)


def stop() -> None:
    worker.require_whale()
    _stop.set()
