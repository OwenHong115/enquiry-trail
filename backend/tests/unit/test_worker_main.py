"""Worker main exit codes: unclean shutdown must exit non-zero."""

from __future__ import annotations

import pytest

from app.worker import runner


class _FakeCrm:
    def close(self):
        raise AssertionError("adapter must not close under live slots")


def test_main_exits_nonzero_on_unclean_shutdown(monkeypatch):
    monkeypatch.setattr(runner, "configure_logging", lambda: None)
    monkeypatch.setattr(runner.Settings, "from_env", classmethod(
        lambda cls: runner.Settings(database_url="x", session_secret="s")))
    monkeypatch.setattr(runner, "make_engine", lambda url: _Engine())
    monkeypatch.setattr(runner, "make_session_factory", lambda engine: (lambda: None))
    monkeypatch.setattr(runner, "_resolve_crm", lambda settings: _FakeCrm())

    def boom(*args, **kwargs):
        raise runner.WorkerUncleanShutdown(0)

    monkeypatch.setattr(runner, "run_forever", boom)
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 1


class _Engine:
    def dispose(self):
        raise AssertionError("engine must not dispose under live slots")
