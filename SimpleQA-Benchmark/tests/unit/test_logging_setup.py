"""Tests for the process-wide logging configuration."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import pytest

from nimble_benchmark.logging_setup import DEFAULT_CONSOLE_LOG_LEVEL, attach_run_log, setup_logging


def _teardown_root_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    # ``setup_logging`` flips this process-wide. Left on, it keeps redirecting
    # ``warnings.warn`` after these tests finish, which would swallow the
    # warnings pytest reports for every later test in the session.
    logging.captureWarnings(False)


@pytest.fixture(autouse=True)
def _restore_root_logging():
    yield
    _teardown_root_handlers()


@pytest.mark.unit
def test_setup_logging_writes_debug_to_file_and_respects_console_level(tmp_path):
    log_file = setup_logging(console_level="WARNING", log_dir=tmp_path)

    logger = logging.getLogger("nimble_benchmark.test_logging")
    logger.debug("debug detail")
    logger.info("info detail")
    logger.warning("warning detail")
    logger.error("error detail")

    content = log_file.read_text(encoding="utf-8")
    assert "debug detail" in content
    assert "info detail" in content
    assert "warning detail" in content
    assert "error detail" in content


@pytest.mark.unit
def test_console_defaults_to_error_while_files_keep_debug(tmp_path: Path) -> None:
    """The console is the only filtered sink. A lane failing every row emits one
    WARNING per question, so the default keeps those off the screen -- but the
    file handler must stay at DEBUG or the information is genuinely lost rather
    than merely quiet."""
    log_file = setup_logging(log_dir=tmp_path)

    root = logging.getLogger()
    console = next(h for h in root.handlers if not isinstance(h, logging.FileHandler))
    file_handler = next(h for h in root.handlers if isinstance(h, logging.FileHandler))

    assert DEFAULT_CONSOLE_LOG_LEVEL == "ERROR"
    assert console.level == logging.ERROR
    assert file_handler.level == logging.DEBUG
    # The root logger must stay at DEBUG: a logger-level filter drops records
    # before any handler sees them, so raising it would silently cap the file.
    assert root.level == logging.DEBUG

    logging.getLogger("nimble_benchmark.test_logging").warning("sampler request failed")
    assert "sampler request failed" in log_file.read_text(encoding="utf-8")


@pytest.mark.unit
def test_warnings_are_captured_into_the_log_file(tmp_path: Path) -> None:
    """``warnings.warn`` writes to stderr on its own, bypassing every sink we
    archive -- a Pydantic serializer warning showed up on the console while
    appearing in neither log file nor errors.md. Routed through logging it is
    recorded like any other WARNING (and stays off the console at the ERROR
    default)."""
    log_file = setup_logging(log_dir=tmp_path)

    warnings.warn("Pydantic serializer warnings: probe", UserWarning, stacklevel=1)

    content = log_file.read_text(encoding="utf-8")
    assert "Pydantic serializer warnings: probe" in content
    assert "py.warnings" in content


@pytest.mark.unit
def test_cli_log_level_default_matches_logging_setup(tmp_path: Path) -> None:
    """The flag default and the module constant must not drift apart."""
    from nimble_benchmark.cli import build_parser

    args = build_parser().parse_args(["--dataset", "simpleqa"])
    assert args.log_level == DEFAULT_CONSOLE_LOG_LEVEL


@pytest.mark.unit
def test_setup_logging_is_idempotent(tmp_path):
    setup_logging(log_dir=tmp_path)
    setup_logging(log_dir=tmp_path)
    root = logging.getLogger()
    # One console + one file handler, not two of each.
    assert len(root.handlers) == 2


@pytest.mark.unit
def test_setup_logging_quiets_noisy_third_party_loggers(tmp_path):
    setup_logging(log_dir=tmp_path)
    for name in ("httpx", "httpcore", "openai", "urllib3", "aiohttp"):
        assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.unit
def test_attach_run_log_mirrors_records_into_run_dir(tmp_path):
    setup_logging(log_dir=tmp_path / "logs")
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    run_log = attach_run_log(run_dir)

    logging.getLogger("nimble_benchmark.test_logging").debug("row failed for query=q")

    assert run_log == run_dir / "eval.log"
    assert "row failed for query=q" in run_log.read_text(encoding="utf-8")
