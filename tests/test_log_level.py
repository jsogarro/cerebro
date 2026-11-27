import io

import structlog

from src.core.observability import configure_logging


def test_log_level_filters_events_below_it() -> None:
    out = io.StringIO()
    configure_logging("info")
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(out))
    try:
        log = structlog.get_logger("t")
        log.debug("hidden_event")
        log.info("shown_event")
    finally:
        structlog.reset_defaults()

    assert "shown_event" in out.getvalue()
    assert "hidden_event" not in out.getvalue()
