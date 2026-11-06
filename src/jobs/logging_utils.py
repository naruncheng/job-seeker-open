"""Stage-boundary logging setup shared by every CLI command."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs full request URLs at INFO, which would put API keys passed as
    # query params (Adzuna) into terminal history and public Actions logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@contextmanager
def stage(logger: logging.Logger, name: str, **counts_in: int) -> Iterator[dict[str, int]]:
    """Log a stage's start, and its duration + counts on exit.

    Usage::

        with stage(logger, "ingest", sources=5) as result:
            ...
            result["jobs"] = len(raw_jobs)
    """
    started = time.monotonic()
    in_desc = ", ".join(f"{k}={v}" for k, v in counts_in.items())
    logger.info("%s: starting (%s)", name, in_desc or "no input")
    result: dict[str, int] = {}
    try:
        yield result
    except Exception:
        duration = time.monotonic() - started
        logger.exception("%s: failed after %.2fs", name, duration)
        raise
    else:
        duration = time.monotonic() - started
        out_desc = ", ".join(f"{k}={v}" for k, v in result.items())
        logger.info("%s: done in %.2fs (%s)", name, duration, out_desc or "no output")
