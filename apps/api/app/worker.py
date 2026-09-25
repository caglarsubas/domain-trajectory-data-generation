"""Run queued jobs outside the API process: `python -m app.worker`."""

from __future__ import annotations

import signal

from app.db import init_db
from app.jobs import Worker
from app.settings import check_startup, load_settings


def main() -> None:
    cfg = load_settings()
    check_startup(cfg)
    init_db(cfg.database_url)
    worker = Worker()
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    print("worker: polling for jobs", flush=True)
    worker.loop()


if __name__ == "__main__":
    main()
