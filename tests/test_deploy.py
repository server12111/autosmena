from io import StringIO

import pytest
from dotenv import dotenv_values

from app.config import Settings
from app.security import verify_password
from deploy.setup import build_env


def test_server_config_without_domain_uses_private_tunnel():
    values = dotenv_values(stream=StringIO(build_env("test-password-123")))
    assert values["ADMIN_ACCESS_MODE"] == "ssh_tunnel"
    assert values["COOKIE_SECURE"] == "false"
    assert "POSTGRES_PASSWORD" not in values
    assert "DATABASE_URL" not in values
    assert verify_password("test-password-123", values["ADMIN_PASSWORD_HASH"])
    assert "test-password-123" not in values.values()
    config = Settings(_env_file=None, **{k.lower(): v for k, v in values.items()})
    assert config.resolved_database_url.startswith("sqlite:")
    config.validate_runtime()


def test_server_config_with_domain_requires_secure_cookies():
    values = dotenv_values(stream=StringIO(build_env("test-password-123", "admin.example.com")))
    assert values["COOKIE_SECURE"] == "true"
    assert values["ADMIN_ACCESS_MODE"] == "https"
    config = Settings(_env_file=None, **{k.lower(): v for k, v in values.items()})
    config.validate_runtime()
    config.cookie_secure = False
    with pytest.raises(ValueError):
        config.validate_runtime()


@pytest.mark.parametrize("domain", ["https://example.com", "example.com/path", "x;echo bad", "127.0.0.1"])
def test_server_domain_rejects_urls_and_shell_input(domain):
    with pytest.raises(ValueError):
        build_env("test-password-123", domain)


def test_domain_added_after_install_preserves_keys_and_database_password():
    from deploy.domain import update_content

    original = "BOT_TOKEN='secret$token'\nPOSTGRES_PASSWORD='keep-me'\nDATABASE_URL='same-url'\nADMIN_DOMAIN=\nCOOKIE_SECURE=false\nADMIN_ACCESS_MODE=ssh_tunnel\n"
    values = dotenv_values(stream=StringIO(update_content(original, "Admin.Example.com")))
    assert values["BOT_TOKEN"] == "secret$token"
    assert values["POSTGRES_PASSWORD"] == "keep-me"
    assert values["DATABASE_URL"] == "same-url"
    assert values["ADMIN_DOMAIN"] == "admin.example.com"
    assert values["COOKIE_SECURE"] == "true"
    assert values["ADMIN_ACCESS_MODE"] == "https"
