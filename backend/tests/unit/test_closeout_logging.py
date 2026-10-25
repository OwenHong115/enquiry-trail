import json
import logging
import sys

from app import logging as app_logging


def test_configuration_is_repeatable_and_keeps_host_capture(capfd, caplog):
    host = logging.StreamHandler(sys.stderr)
    root = logging.getLogger()
    root.addHandler(host)
    try:
        app_logging.configure_logging()
        app_logging.configure_logging()
        with caplog.at_level(logging.INFO):
            app_logging.log_event("closeout.test", outcome="ok")
        captured = capfd.readouterr()
        records = [json.loads(line) for line in captured.err.splitlines()
                   if line.startswith("{")]
        assert [r for r in records if r.get("event") == "closeout.test"] == [
            {"event": "closeout.test", "outcome": "ok"}]
        assert any('"event": "closeout.test"' in r.getMessage()
                   for r in caplog.records)
    finally:
        root.removeHandler(host)
        host.close()
