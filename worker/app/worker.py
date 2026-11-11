"""Osobny proces workera: `python -m app.worker`. Przetwarza kolejkę poza requestami HTTP."""

from __future__ import annotations

import logging
import signal
import threading

from .api import _worker_loop
from .config import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings.validate()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.getLogger("mdb.jobs").info("worker started")
    _worker_loop(stop)


if __name__ == "__main__":
    main()
