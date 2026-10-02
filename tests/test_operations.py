import socket
import ssl
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError
from pydantic import ValidationError
from pymongo.errors import ServerSelectionTimeoutError

from app.errors import KNOWN_VALUE_ERRORS, failure_hint
from app.models import Draft
from app.worker import main


def test_worker_job_executes_one_cycle(monkeypatch):
    operation = AsyncMock()
    monkeypatch.setattr("app.worker.run", operation)
    monkeypatch.setattr("sys.argv", ["worker_job.py"])
    main(default_once=True)
    operation.assert_awaited_once_with(once=True)


def test_worker_job_returns_failure_without_sensitive_traceback(monkeypatch, caplog):
    import pytest

    operation = AsyncMock(side_effect=ServerSelectionTimeoutError("getaddrinfo failed SECRET_URI"))
    monkeypatch.setattr("app.worker.run", operation)
    monkeypatch.setattr("sys.argv", ["worker_job.py"])
    with pytest.raises(SystemExit) as exc:
        main(default_once=True)
    assert exc.value.code == 1
    assert "DNS" in caplog.text
    assert "SECRET_URI" not in caplog.text


def test_failure_hints_never_echo_exception_content():
    assert "SECRET_TOKEN" not in failure_hint(ValueError("SECRET_TOKEN"))
    assert "DNS" in failure_hint(ServerSelectionTimeoutError("getaddrinfo failed"))
    assert "Network Access" in failure_hint(ServerSelectionTimeoutError("connection refused"))


@pytest.mark.parametrize("message,hint", KNOWN_VALUE_ERRORS.items())
def test_known_failures_report_actionable_cause(message, hint):
    assert failure_hint(ValueError(message)) == hint
    # Não aceita correspondências parciais que possam carregar dados sensíveis.
    assert "SECRET_TOKEN" not in failure_hint(ValueError(message + " SECRET_TOKEN"))


def test_validation_failure_reports_fields_without_generated_content():
    with pytest.raises(ValidationError) as exc:
        Draft(
            title="Post",
            caption="SECRET_TOKEN" * 300,
            hashtags=[],
            image_brief="Arte",
            topic="Software",
            source_urls=["https://site/"],
        )
    hint = failure_hint(exc.value)
    assert "caption" in hint
    assert "SECRET_TOKEN" not in hint


def test_validation_failure_does_not_echo_unknown_field_names():
    exc = ValidationError.from_exception_data(
        "SECRET_MODEL",
        [{"type": "missing", "loc": ("SECRET_FIELD",), "input": "SECRET_TOKEN"}],
    )
    assert "SECRET" not in failure_hint(exc)


@pytest.mark.parametrize(
    "cause,expected",
    [
        (socket.gaierror("SECRET_DNS"), "DNS"),
        (ssl.SSLCertVerificationError("SECRET_TLS"), "certificado TLS"),
        (ConnectionRefusedError("SECRET_REFUSED"), "conexão recusada"),
        (ConnectionResetError("SECRET_RESET"), "conexão interrompida"),
        (OSError("SECRET_OTHER"), "sem conexão HTTPS"),
    ],
)
def test_connection_failure_reports_service_and_safe_cause(cause, expected):
    request = httpx.Request("GET", "https://zionsoftwarehouse.com.br/robots.txt?token=SECRET_QUERY")
    exc = httpx.ConnectError("SECRET_MESSAGE", request=request)
    exc.__cause__ = cause
    hint = failure_hint(exc)
    assert "Site Zion" in hint
    assert expected in hint
    assert "SECRET" not in hint


def test_connection_failure_without_request_is_safe():
    assert "HTTP: sem conexão HTTPS" in failure_hint(httpx.ConnectError("SECRET"))


def test_connection_failure_does_not_echo_unknown_host():
    exc = httpx.ConnectError(
        "SECRET", request=httpx.Request("GET", "https://SECRET.example/?token=SECRET")
    )
    assert "SECRET" not in failure_hint(exc)


def test_openai_connection_failure_reports_nested_dns_cause():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    cause = httpx.ConnectError("SECRET", request=request)
    cause.__cause__ = socket.gaierror("SECRET_DNS")
    exc = APIConnectionError(request=request)
    exc.__cause__ = cause
    assert "OpenAI: falha de DNS" in failure_hint(exc)
    assert "SECRET" not in failure_hint(exc)


@pytest.mark.parametrize(
    "error,expected",
    [(httpx.ConnectTimeout, "tempo"), (httpx.ReadTimeout, "tempo"), (httpx.ProxyError, "proxy")],
)
def test_timeout_and_proxy_failure_are_safe(error, expected):
    request = httpx.Request("GET", "https://graph.instagram.com/?token=SECRET")
    hint = failure_hint(error("SECRET", request=request))
    assert "Instagram" in hint
    assert expected in hint
    assert "SECRET" not in hint


def test_http_status_failure_does_not_echo_url_or_response():
    request = httpx.Request("GET", "https://zionsoftwarehouse.com.br/?token=SECRET")
    response = httpx.Response(403, request=request, text="SECRET_BODY")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        response.raise_for_status()
    assert failure_hint(exc.value) == "Site Zion: resposta HTTP 403"
