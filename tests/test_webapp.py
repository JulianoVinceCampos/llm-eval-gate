"""Dashboard: pure handlers first, then the real server over a socket."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import pytest

from llm_eval_gate.pipeline import Project
from llm_eval_gate.webapp import (
    SECURITY_HEADERS,
    SESSION_COOKIE,
    AppState,
    Credentials,
    LoginThrottle,
    build_state,
    credentials_ok,
    handle_get,
    handle_post,
    make_server,
    make_session,
    parse_margin,
    read_session,
)

USER = "julianovincedecampos"


@pytest.fixture(scope="module")
def state(project: Project) -> AppState:
    return build_state(project, credentials=Credentials(USER, "pw"), secret=b"k" * 32, env={})


def get(
    state: AppState, path: str, user: str | None = USER, **query: str
) -> tuple[HTTPStatus, dict[str, Any]]:
    return handle_get(state, path, query, user)


# --- pure handlers --------------------------------------------------------------------


def test_public_endpoints_need_no_session(state: AppState) -> None:
    assert get(state, "/api/health", user=None)[0] is HTTPStatus.OK
    status, body = get(state, "/api/session", user=None)
    assert (status, body["authenticated"]) == (HTTPStatus.OK, False)


@pytest.mark.parametrize(
    "path", ["/api/overview", "/api/labels", "/api/calibration", "/api/cases", "/api/whatif"]
)
def test_private_endpoints_need_a_session(state: AppState, path: str) -> None:
    assert get(state, path, user=None)[0] is HTTPStatus.UNAUTHORIZED


def test_overview(state: AppState) -> None:
    status, body = get(state, "/api/overview")
    assert status is HTTPStatus.OK
    assert body["splits"] == {"in-dist": 200, "hard": 300}
    assert body["policy"]["method"] == "tango"
    assert {c["name"] for c in body["candidates"]} == {
        "ollama-qwen2.5-0.5b",
        "rules-signature",
        "synthetic-calibrated",
    }
    assert len(body["scenarios"]) == 4
    rules = next(c for c in body["candidates"] if c["name"] == "rules-signature")
    assert "reference" in rules and "trace" not in rules


def test_candidate_case_and_labels(state: AppState) -> None:
    status, body = get(state, "/api/candidate", name="rules-signature")
    assert status is HTTPStatus.OK
    assert body["metrics"]["hard"]["n_cases"] == 300
    assert body["reference"]["fingerprint"]
    assert get(state, "/api/candidate", name="nope")[0] is HTTPStatus.NOT_FOUND
    cases = get(
        state, "/api/cases", split="hard", candidate="rules-signature", errors="1", limit="5"
    )[1]
    assert cases["total"] > 100
    assert len(cases["rows"]) == 5
    assert all(row["predicted"] != row["label"] for row in cases["rows"])
    case_id = cases["rows"][0]["id"]
    status, case = get(state, "/api/case", id=case_id)
    assert status is HTTPStatus.OK
    assert "rules-signature" in case["predictions"]
    assert get(state, "/api/case", id="c000000000000")[0] is HTTPStatus.NOT_FOUND
    assert len(get(state, "/api/labels")[1]["labels"]) == 9
    assert get(state, "/api/calibration")[1]["available"] is True
    assert get(state, "/api/nope")[0] is HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "query",
    [
        {"split": "train"},
        {"label": "pool"},
        {"candidate": "ghost"},
        {"limit": "0"},
        {"limit": "101"},
        {"offset": "-1"},
        {"offset": "x"},
    ],
)
def test_cases_validates_every_parameter(state: AppState, query: dict[str, str]) -> None:
    assert get(state, "/api/cases", **query)[0] is HTTPStatus.BAD_REQUEST


def test_whatif_redecides_with_the_ci_code(state: AppState) -> None:
    status, body = get(
        state,
        "/api/whatif",
        name="synthetic-calibrated",
        split="hard",
        margin="0.03",
        alpha="0.05",
        n="0",
    )
    assert status is HTTPStatus.OK
    assert (body["verdict"], body["used"], body["available"]) == ("pass", 300, 300)
    assert body["method"] == "tango"
    small = get(
        state,
        "/api/whatif",
        name="synthetic-calibrated",
        split="hard",
        margin="0.03",
        alpha="0.05",
        n="20",
    )[1]
    assert (small["verdict"], small["used"]) == ("inconclusive", 20)
    again = get(
        state,
        "/api/whatif",
        name="synthetic-calibrated",
        split="hard",
        margin="0.03",
        alpha="0.05",
        n="20",
    )[1]
    assert again is small  # cached
    assert get(state, "/api/whatif", name="ollama-qwen2.5-0.5b")[0] is HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "query",
    [
        {"margin": "0.0301"},
        {"margin": "0.3"},
        {"margin": "abc"},
        {"alpha": "0.2"},
        {"alpha": "x"},
        {"n": "30"},
        {"split": "train"},
    ],
)
def test_whatif_accepts_only_the_closed_grid(state: AppState, query: dict[str, str]) -> None:
    status, body = get(state, "/api/whatif", name="synthetic-calibrated", **query)
    assert status is HTTPStatus.BAD_REQUEST, body


def test_parse_margin_grid() -> None:
    assert parse_margin("0.03") == 0.03
    assert parse_margin("0") == 0.0
    assert parse_margin("0.2") == 0.2


# --- session and login ----------------------------------------------------------------


def test_session_tokens_are_signed_and_expire() -> None:
    token = make_session(b"s" * 32, USER, now=1000.0)
    assert read_session(b"s" * 32, token, now=1001.0) == USER
    assert read_session(b"t" * 32, token, now=1001.0) is None
    assert read_session(b"s" * 32, token, now=1000.0 + 9 * 3600) is None
    user, expiry, signature = token.split(".")
    assert read_session(b"s" * 32, f"{user}.{int(expiry) + 1}.{signature}", now=1001.0) is None
    assert read_session(b"s" * 32, "garbage", now=1001.0) is None


def test_credentials() -> None:
    creds = Credentials.from_env({"LEG_USER": "u", "LEG_PASSWORD": "p"})
    assert credentials_ok(creds, "u", "p")
    assert not credentials_ok(creds, "u", "P")
    assert Credentials.from_env({}).user == USER


def test_login_logout_and_throttle(state: AppState) -> None:
    status, body, token = handle_post(
        state, "/api/login", {"user": USER, "password": "pw"}, "1.1.1.1"
    )
    assert status is HTTPStatus.OK and body["user"] == USER
    assert token and read_session(state.secret, token) == USER
    status, _, token = handle_post(state, "/api/login", {"user": USER, "password": "no"}, "1.1.1.1")
    assert (status, token) == (HTTPStatus.UNAUTHORIZED, None)
    assert handle_post(state, "/api/logout", {}, "1.1.1.1")[2] == ""
    assert handle_post(state, "/api/other", {}, "1.1.1.1")[0] is HTTPStatus.NOT_FOUND
    ticks = iter(range(100))
    throttle = LoginThrottle(window_s=60, max_attempts=3, clock=lambda: float(next(ticks)))
    assert [throttle.allow("x") for _ in range(4)] == [True, True, True, False]
    assert throttle.allow("y")


# --- real server ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def server(state: AppState) -> Iterator[tuple[str, int]]:
    httpd = make_server(state, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", int(httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


def request(
    server: tuple[str, int],
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(*server, timeout=10)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    result = response.status, {k.lower(): v for k, v in response.getheaders()}, data
    connection.close()
    return result


def test_static_files_carry_the_security_headers(server: tuple[str, int]) -> None:
    status, headers, body = request(server, "GET", "/")
    assert status == 200
    assert b"<html" in body.lower()
    for header in SECURITY_HEADERS:
        assert header.lower() in headers
    assert "strict-transport-security" not in headers
    assert request(server, "GET", "/app.js")[0] == 200
    assert request(server, "GET", "/styles.css")[0] == 200
    status, _, body = request(server, "HEAD", "/")
    assert (status, body) == (200, b"")
    assert request(server, "GET", "/../../etc/passwd")[0] == 404


def test_login_flow_over_http(server: tuple[str, int]) -> None:
    assert request(server, "GET", "/api/overview")[0] == 401
    payload = json.dumps({"user": USER, "password": "pw"}).encode()
    status, headers, _ = request(
        server, "POST", "/api/login", payload, {"Content-Type": "application/json"}
    )
    assert status == 200
    cookie = headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie
    token = cookie.split(";")[0]
    status, _, body = request(server, "GET", "/api/overview", headers={"Cookie": token})
    assert status == 200
    assert json.loads(body)["splits"]["hard"] == 300


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        (b"{}", {"Content-Type": "text/plain"}, 415),
        (b"{}", {"Content-Type": "application/json", "Origin": "http://evil.example"}, 403),
        (b"{bad", {"Content-Type": "application/json"}, 400),
        (b"[1]", {"Content-Type": "application/json"}, 400),
        # Declared size alone is refused before a byte of body is read.
        (None, {"Content-Type": "application/json", "Content-Length": "20000"}, 413),
        (None, {"Content-Type": "application/json", "Content-Length": "abc"}, 413),
    ],
)
def test_post_guards(
    server: tuple[str, int], body: bytes | None, headers: dict[str, str], expected: int
) -> None:
    assert request(server, "POST", "/api/login", body, headers)[0] == expected


def test_trusted_proxy_adds_hsts_and_secure_cookie(project: Project) -> None:
    proxied = build_state(
        project, credentials=Credentials(USER, "pw"), secret=b"k" * 32, env={"LEG_TRUST_PROXY": "1"}
    )
    httpd = make_server(proxied, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        address = ("127.0.0.1", int(httpd.server_address[1]))
        forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.7, 192.0.2.1"}
        _, headers, _ = request(address, "GET", "/", headers=forwarded)
        assert headers["strict-transport-security"].startswith("max-age=")
        payload = json.dumps({"user": USER, "password": "pw"}).encode()
        _, headers, _ = request(
            address,
            "POST",
            "/api/login",
            payload,
            {"Content-Type": "application/json", **forwarded},
        )
        assert "Secure" in headers["set-cookie"]
        assert SESSION_COOKIE in headers["set-cookie"]
    finally:
        httpd.shutdown()
        httpd.server_close()
