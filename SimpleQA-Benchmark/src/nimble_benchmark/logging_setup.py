"""Process-wide logging configuration for the CLI entrypoints.

Two sinks with different verbosity:

* Console -- human-facing output at ``--log-level``
  (:data:`DEFAULT_CONSOLE_LOG_LEVEL`, ERROR), same concise format the CLI
  always printed. ERROR rather than INFO because the console is the only
  *filtered* sink: a lane failing every row emits one WARNING per question,
  which drowns the run for information already captured verbatim in the log
  file and summarized in ``errors.md``. Raise it to INFO for progress
  commentary and the lane-exclusion warnings.
* File -- everything at DEBUG, with timestamps and source locations, so a
  failed n=500 run can be diagnosed after the fact without re-running.
  One rotating file per invocation under ``--log-dir`` (default ``logs/``),
  plus an optional per-run mirror inside the run directory (see
  :func:`attach_run_log`) so each ``runs/run_*`` folder is a self-contained
  debugging artifact next to its CSVs.

Third-party libraries are pinned to WARNING (:data:`NOISY_LOGGERS`): at DEBUG
they log every request, socket, connection-pool event, and -- in numba's case
-- their own compiler IR, which buries the eval's own signal and bloats the
file by orders of magnitude. Genuine problems (429 storms, connection resets)
still surface as WARNING+.

``warnings.warn`` output is routed into logging as well
(:func:`logging.captureWarnings`). Python writes warnings straight to stderr by
default, which put them in the one place nothing archives: not the rotating log
file, not ``<run-dir>/eval.log``, not ``errors.md``. That was invisible while
the console echoed INFO, but with the console filtered to ERROR a stray
``UserWarning`` became the loudest thing on screen *and* the only unrecorded
one. Routed through logging they land in both files at WARNING and stay off the
console at the default level, like every other WARNING.
"""

from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

# Single source of truth for the console threshold; ``cli`` imports it as the
# ``--log-level`` default so the flag and this module can never disagree.
DEFAULT_CONSOLE_LOG_LEVEL: Final[str] = "ERROR"

CONSOLE_FORMAT: Final[str] = "%(levelname)s %(name)s %(message)s"
FILE_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"

# Measured on the 2026-08-08 n=500 x 14-lane run, whose log was 21 MB: numba
# alone contributed 13.1 MB and the Anthropic SDK 1.1 MB, leaving only 6.2 MB
# of actual eval output. ``numba`` is pulled in transitively by ``ranx`` for
# the NDCG kernels and dumps its bytecode/SSA IR at DEBUG (``numba.core.byteflow``
# emitted 57k lines by itself); the provider SDKs log every request envelope.
# Pinning the parent logger covers all child loggers under it.
NOISY_LOGGERS: Final[tuple[str, ...]] = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
    "aiohttp",
    "numba",
    "firecrawl",
)

# A DEBUG-level file on a 500-row x N-sampler run stays well under this, but
# a retry storm against a dead upstream could log for hours -- rotate so a
# pathological run can't fill the disk.
_MAX_LOG_BYTES: Final[int] = 50 * 1024 * 1024
_BACKUP_COUNT: Final[int] = 3


def setup_logging(
    *,
    console_level: str = DEFAULT_CONSOLE_LOG_LEVEL,
    log_dir: str | Path = "logs",
    timestamp: datetime | None = None,
) -> Path:
    """Configure root logging with a console handler and a DEBUG file handler.

    Returns the path of the log file for this invocation. Idempotent: calling
    again replaces the previous handlers instead of stacking duplicates
    (matters for tests and for repeated in-process CLI invocations).
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    ts = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    log_file = directory / f"nimble-eval_{ts}.log"
    file_handler = RotatingFileHandler(log_file, maxBytes=_MAX_LOG_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(file_handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Redirect ``warnings.warn`` into the ``py.warnings`` logger so warnings
    # reach both files instead of only stderr. See the module docstring.
    logging.captureWarnings(True)

    return log_file


def attach_run_log(run_dir: str | Path, filename: str = "eval.log") -> Path:
    """Mirror all subsequent DEBUG+ records into ``run_dir/eval.log``.

    Called once the run directory exists (it is created after preflight), so
    the run artifact carries its own log next to the raw CSVs. Opened in append
    mode, so pointing two invocations at the same ``--run-dir`` keeps both
    logs rather than truncating the first.
    """
    path = Path(run_dir) / filename
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    logging.getLogger().addHandler(handler)
    return path
