import pytest

from mcp_server.config import Config, ConfigError

REQUIRED = ["PICO_BASE_URL", "MCP_ADMIN_EMAIL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MCP_PUBLIC_URL"]


def _set_required_env(monkeypatch):
    monkeypatch.setenv("PICO_BASE_URL", "http://192.168.1.18")
    monkeypatch.setenv("MCP_ADMIN_EMAIL", "Admin@Gmail.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.delenv("MCP_AUTH_PASSWORD", raising=False)


def test_from_env_reads_required_and_default_values(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_STORE_PATH", raising=False)
    monkeypatch.delenv("MCP_USERS_PATH", raising=False)
    monkeypatch.delenv("MCP_SERVER_PORT", raising=False)
    _set_required_env(monkeypatch)

    config = Config.from_env()

    assert config.pico_base_url == "http://192.168.1.18"
    assert config.admin_email == "admin@gmail.com"
    assert config.google_client_id == "client-id"
    assert config.google_client_secret == "client-secret"
    assert config.public_url == "https://mcp.example.com"
    assert str(config.token_store_path) == "data/tokens.json"
    assert str(config.users_path) == "data/users.json"
    assert config.port == 8420


def test_from_env_strips_trailing_slashes(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PICO_BASE_URL", "http://192.168.1.18/")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/")

    config = Config.from_env()

    assert config.pico_base_url == "http://192.168.1.18"
    assert config.public_url == "https://mcp.example.com"


def test_from_env_reads_overridden_values(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_TOKEN_STORE_PATH", "/tmp/tokens.json")
    monkeypatch.setenv("MCP_USERS_PATH", "/tmp/users.json")
    monkeypatch.setenv("MCP_SERVER_PORT", "9000")

    config = Config.from_env()

    assert str(config.token_store_path) == "/tmp/tokens.json"
    assert str(config.users_path) == "/tmp/users.json"
    assert config.port == 9000


@pytest.mark.parametrize("missing", REQUIRED)
def test_from_env_raises_on_missing_required_value(monkeypatch, missing):
    _set_required_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ConfigError):
        Config.from_env()


def test_from_env_rejects_a_malformed_admin_email(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_ADMIN_EMAIL", "not-an-email")

    with pytest.raises(ConfigError):
        Config.from_env()
