import pytest

from app.adapters.crm_http import HttpCrmAdapter
from app.settings import Settings
from app.worker.runner import _resolve_crm

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"


def test_http_mode_missing_config_fails():
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", crm_mode="http")


def test_http_mode_resolves_adapter():
    s = Settings(database_url=DB, session_secret="s", crm_mode="http",
                 crm_base_url="http://127.0.0.1:8091", crm_token="t")
    assert isinstance(_resolve_crm(s), HttpCrmAdapter)


def test_mock_mode_resolves_adapter_not_fake():
    s = Settings(database_url=DB, session_secret="s")
    assert isinstance(_resolve_crm(s), HttpCrmAdapter)


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", crm_mode="bogus")
