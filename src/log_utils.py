"""
Shared logging setup for CMA Python.
"""
import datetime
import logging
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.environ.get("CMA_LOG_LEVEL", "INFO").upper()

_formatter = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(LOG_LEVEL)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_formatter)
    log.addHandler(handler)

    try:
        fh = logging.FileHandler(
            LOG_DIR / f"cma_{datetime.date.today():%Y%m%d}.log",
            encoding="utf-8",
        )
        fh.setFormatter(_formatter)
        log.addHandler(fh)
    except OSError:
        pass

    return log
