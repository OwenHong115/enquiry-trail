"""JSON application logs; process entrypoints configure output explicitly."""
from __future__ import annotations

import json
import logging
import sys
import threading

_CONFIG_LOCK = threading.RLock()


class _ApplicationOutput(logging.Handler):
    """Resolve stderr at emission, so repeated app/test lifetimes are safe."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


class _OtherLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.name == "leadops" or record.name.startswith("leadops."))


def _console_handler(handler: logging.Handler) -> bool:
    if not isinstance(handler, logging.StreamHandler):
        return False
    stream = handler.stream
    if stream in (sys.stderr, sys.stdout, sys.__stderr__, sys.__stdout__):
        return True
    try:
        return stream.fileno() in (1, 2)
    except (AttributeError, OSError, ValueError):
        return False


def configure_logging() -> None:
    """Own one JSON console sink, preserving host logs and capture handlers.

    Call after host logging setup. Root console sinks exclude these records;
    propagation still reaches non-console handlers such as caplog and files.
    Importing this module never modifies logging configuration.
    """
    with _CONFIG_LOCK:
        logger = logging.getLogger("leadops")
        logger.disabled = False
        logger.setLevel(logging.INFO)
        logger.propagate = True
        owned = next((h for h in logger.handlers if isinstance(h, _ApplicationOutput)), None)
        if owned is None:
            owned = _ApplicationOutput()
            owned.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(owned)
        for handler in [*logger.handlers, *logging.getLogger().handlers]:
            if _console_handler(handler) and not any(isinstance(f, _OtherLogs) for f in handler.filters):
                handler.addFilter(_OtherLogs())


def log_event(event: str, **fields: object) -> None:
    """Emit identifiers/outcomes only; callers must exclude secrets and PII."""
    logging.getLogger("leadops").info(json.dumps({"event": event, **fields}, default=str))
