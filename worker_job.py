"""Entrada para cron cPanel: /caminho/virtualenv/bin/python /caminho/zion/worker_job.py."""

import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

from app.worker import main  # noqa: E402

if __name__ == "__main__":
    main(default_once=True)
