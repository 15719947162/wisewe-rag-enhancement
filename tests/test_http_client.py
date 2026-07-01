from __future__ import annotations

from core import http_client


def test_should_trust_env_proxy_without_proxy(monkeypatch) -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "HTTP_CLIENT_TRUST_ENV"):
        monkeypatch.delenv(key, raising=False)

    assert http_client.should_trust_env_proxy() is True


def test_should_not_trust_docker_proxy_outside_container(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://host.docker.internal:7890")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_CLIENT_TRUST_ENV", raising=False)
    monkeypatch.setattr(http_client, "_is_running_in_container", lambda: False)

    assert http_client.should_trust_env_proxy() is False


def test_proxy_override_can_force_trust_env(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://host.docker.internal:7890")
    monkeypatch.setenv("HTTP_CLIENT_TRUST_ENV", "true")
    monkeypatch.setattr(http_client, "_is_running_in_container", lambda: False)

    assert http_client.should_trust_env_proxy() is True


def test_should_not_trust_docker_proxy_inside_container_by_default(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://host.docker.internal:7890")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_CLIENT_TRUST_ENV", raising=False)
    monkeypatch.setattr(http_client, "_is_running_in_container", lambda: True)

    assert http_client.should_trust_env_proxy() is False
