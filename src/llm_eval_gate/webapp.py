"""Dashboard: stdlib HTTP server, read-only, behind a demo session gate.

Same shape as the postmortem-miner dashboard (ADR-0003): `http.server`, a signed session
cookie, a fixed map of static files, and route handlers that are pure functions returning
(status, payload), so the whole API is testable without a socket.

The one computation exposed to the network is the what-if (re-deciding the gate with
another margin, alpha or sample size). Its inputs are validated against a closed grid and
its results are cached, so the endpoint cannot be turned into a CPU amplifier.

The login is a demonstration gate over synthetic, read-only data. It is enforced on the
server with an HMAC-signed cookie, because a gate validated in the browser is no gate.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from llm_eval_gate import __version__
from llm_eval_gate.calibration import CalibrationResult
from llm_eval_gate.config import load_candidates
from llm_eval_gate.domain import EVAL_SPLITS, Case
from llm_eval_gate.factory import ProviderMode
from llm_eval_gate.gate import non_inferiority, pair_stats
from llm_eval_gate.labels import CATALOG, parse_label
from llm_eval_gate.pipeline import (
    Project,
    Workspace,
    baseline_comparisons,
    committed_calibration,
    evaluate_candidate,
    load_workspace,
    run_scenarios,
)
from llm_eval_gate.results import CandidateResult, Comparison
from llm_eval_gate.rng import Rng
from llm_eval_gate.scenarios import ScenarioOutcome

WEB_ROOT = Path(__file__).resolve().parent / "web"
SESSION_COOKIE = "leg_session"
SESSION_TTL_SECONDS = 8 * 3600
DEFAULT_USER = "julianovincedecampos"
DEFAULT_PASSWORD = "llm-eval-gate"  # noqa: S105 - documented demo credential, overridable
MAX_BODY_BYTES = 16 * 1024
PUBLIC_API = frozenset({"/api/health", "/api/login", "/api/session", "/api/logout"})

# Closed grid for the what-if. Anything else is a 400.
ALPHAS = (0.01, 0.025, 0.05, 0.10)
SIZES = (0, 20, 50, 100, 200)  # 0 = every paired case
MARGIN_MAX_MILLI = 200
MARGIN_STEP_MILLI = 5
WHATIF_RESAMPLES = 1000
WHATIF_CACHE_SIZE = 512
MAX_PAGE = 100

LOGIN_WINDOW_S = 60.0
LOGIN_MAX_ATTEMPTS = 10
THROTTLE_MAX_KEYS = 10_000

Payload = dict[str, Any]
Response = tuple[HTTPStatus, Payload]


class BadRequestError(ValueError):
    """A query parameter outside its closed domain."""


@dataclass(frozen=True, slots=True)
class Credentials:
    user: str
    password: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Credentials:
        source = env if env is not None else os.environ
        return cls(
            user=source.get("LEG_USER") or DEFAULT_USER,
            password=source.get("LEG_PASSWORD") or DEFAULT_PASSWORD,
        )


class LoginThrottle:
    """Sliding window per client. Slows brute force on the demo gate, bounded in memory."""

    def __init__(
        self,
        window_s: float = LOGIN_WINDOW_S,
        max_attempts: int = LOGIN_MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_s
        self._max = max_attempts
        self._clock = clock
        self._attempts: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            recent = [t for t in self._attempts.pop(key, []) if now - t < self._window]
            allowed = len(recent) < self._max
            if allowed:
                recent.append(now)
            self._attempts[key] = recent
            while len(self._attempts) > THROTTLE_MAX_KEYS:
                self._attempts.popitem(last=False)
            return allowed


@dataclass(slots=True)
class AppState:
    workspace: Workspace
    results: list[CandidateResult]
    comparisons: list[Comparison]
    scenarios: list[ScenarioOutcome]
    calibration: CalibrationResult | None
    credentials: Credentials
    secret: bytes
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    trust_proxy: bool = False
    _cache: OrderedDict[tuple[Any, ...], Payload] = field(default_factory=OrderedDict)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cases_by_id(self) -> dict[str, Case]:
        return {case.id: case for case in self.workspace.cases}

    def result(self, name: str) -> CandidateResult | None:
        return next((r for r in self.results if r.name == name), None)


def build_state(
    project: Project,
    *,
    credentials: Credentials | None = None,
    secret: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> AppState:
    source = env if env is not None else os.environ
    ws = load_workspace(project)
    results = [
        evaluate_candidate(ws, config, ProviderMode.REPLAY)
        for config in load_candidates(project.candidates_dir)
    ]
    calibration, _ = committed_calibration(project)
    return AppState(
        workspace=ws,
        results=results,
        comparisons=baseline_comparisons(ws, results),
        scenarios=run_scenarios(ws),
        calibration=calibration,
        credentials=credentials or Credentials.from_env(source),
        secret=secret or secrets.token_bytes(32),
        trust_proxy=source.get("LEG_TRUST_PROXY") == "1",
    )


# --- session --------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode("ascii"), sha256).digest())


def make_session(secret: bytes, user: str, now: float | None = None) -> str:
    expiry = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{_b64(user.encode('utf-8'))}.{expiry}"
    return f"{payload}.{_sign(secret, payload)}"


def read_session(secret: bytes, token: str, now: float | None = None) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_part, expiry_part, signature = parts
    payload = f"{user_part}.{expiry_part}"
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None
    try:
        expiry = int(expiry_part)
        user = _unb64(user_part).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if expiry < (now if now is not None else time.time()):
        return None
    return user


def credentials_ok(credentials: Credentials, user: str, password: str) -> bool:
    user_ok = hmac.compare_digest(user.encode("utf-8"), credentials.user.encode("utf-8"))
    password_ok = hmac.compare_digest(
        password.encode("utf-8"), credentials.password.encode("utf-8")
    )
    return user_ok and password_ok


# --- payloads (pure) ------------------------------------------------------------------


def _result_row(result: CandidateResult) -> Payload:
    row = result.summary_dict()
    row.pop("trace", None)
    row.pop("report", None)
    if result.reference is not None:
        row["reference"] = {
            "accepted_at": result.reference.accepted_at,
            "source_commit": result.reference.source_commit,
            "note": result.reference.note,
        }
    return row


def overview_payload(state: AppState) -> Payload:
    ws = state.workspace
    policy = ws.policy.regression
    return {
        "version": __version__,
        "dataset_sha256": ws.dataset_sha256,
        "splits": {
            split: sum(1 for case in ws.cases if case.split == split) for split in EVAL_SPLITS
        },
        "policy": {
            "alpha": policy.alpha,
            "margin": policy.margin,
            "method": policy.method,
            "on_inconclusive": policy.on_inconclusive,
            "splits": list(policy.splits),
        },
        "spec": {
            "id": ws.spec.id,
            "version": ws.spec.version,
            "title": ws.spec.title,
            "requirements": len(ws.spec.requirements),
        },
        "candidates": [_result_row(result) for result in state.results],
        "scenarios": [outcome.to_dict() for outcome in state.scenarios],
        "comparisons": [comparison.to_dict() for comparison in state.comparisons],
    }


def candidate_payload(state: AppState, name: str) -> Payload | None:
    result = state.result(name)
    if result is None:
        return None
    payload = result.summary_dict()
    payload["metrics"] = {split: m.to_dict() for split, m in result.metrics.items()}
    if result.reference is not None:
        payload["reference"] = {
            "accepted_at": result.reference.accepted_at,
            "source_commit": result.reference.source_commit,
            "note": result.reference.note,
            "fingerprint": result.reference.run.fingerprint,
        }
    return payload


def parse_margin(raw: str) -> float:
    try:
        milli = round(float(raw) * 1000)
    except ValueError:
        raise BadRequestError("margin must be a number") from None
    if not 0 <= milli <= MARGIN_MAX_MILLI or milli % MARGIN_STEP_MILLI:
        raise BadRequestError("margin must be a multiple of 0.005 in [0, 0.2]")
    if abs(float(raw) * 1000 - milli) > 1e-6:
        raise BadRequestError("margin must be a multiple of 0.005 in [0, 0.2]")
    return milli / 1000


def parse_choice(raw: str, allowed: tuple[float, ...] | tuple[int, ...], name: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise BadRequestError(f"{name} must be a number") from None
    for option in allowed:
        if abs(value - option) < 1e-12:
            return float(option)
    raise BadRequestError(f"{name} must be one of {list(allowed)}")


def whatif_payload(
    state: AppState, name: str, split: str, margin: float, alpha: float, size: int
) -> Payload | None:
    key = (name, split, round(margin * 1000), alpha, size)
    with state._cache_lock:
        cached = state._cache.get(key)
        if cached is not None:
            state._cache.move_to_end(key)
            return cached
    result = state.result(name)
    if result is None or result.reference is None or not result.run_stats:
        return None
    pairs = pair_stats(result.reference_stats, result.run_stats, split)
    available = len(pairs)
    if not pairs:
        return None
    if size and size < available:
        pairs = Rng("whatif", name, split, size).sample(pairs, size)
    policy = state.workspace.policy.regression
    decision = non_inferiority(
        pairs,
        split=split,
        margin=margin,
        alpha=alpha,
        method=policy.method,
        resamples=min(policy.resamples, WHATIF_RESAMPLES),
        seed=f"whatif|{name}|{split}|{size}",
    )
    payload = decision.to_dict() | {"available": available, "used": len(pairs), "candidate": name}
    with state._cache_lock:
        state._cache[key] = payload
        while len(state._cache) > WHATIF_CACHE_SIZE:
            state._cache.popitem(last=False)
    return payload


def cases_payload(state: AppState, query: Mapping[str, str]) -> Payload:
    split = query.get("split", "")
    if split and split not in EVAL_SPLITS:
        raise BadRequestError("unknown split")
    label = query.get("label", "")
    if label and parse_label(label) is None:
        raise BadRequestError("unknown label")
    name = query.get("candidate", "")
    result = state.result(name) if name else None
    if name and result is None:
        raise BadRequestError("unknown candidate")
    errors_only = query.get("errors") == "1"
    try:
        offset = int(query.get("offset", "0"))
        limit = int(query.get("limit", "50"))
    except ValueError:
        raise BadRequestError("offset and limit must be integers") from None
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise BadRequestError(f"offset >= 0 and 1 <= limit <= {MAX_PAGE}")
    rows: list[Payload] = []
    for case in state.workspace.cases:
        if split and case.split != split:
            continue
        if label and case.label.value != label:
            continue
        stat = result.run_stats.get(case.id) if result is not None else None
        if errors_only and (stat is None or stat.majority_correct):
            continue
        rows.append(
            {
                "id": case.id,
                "split": case.split,
                "label": case.label.value,
                "lang": case.lang,
                "operators": list(case.operators),
                "predicted": stat.majority if stat is not None else None,
                "correct_rate": stat.correct_rate if stat is not None else None,
            }
        )
    return {
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rows": rows[offset : offset + limit],
    }


def case_payload(state: AppState, case_id: str) -> Payload | None:
    case = state.cases_by_id.get(case_id)
    if case is None:
        return None
    predictions = {
        result.name: {
            "majority": stat.majority,
            "predictions": list(stat.predictions),
            "correct_rate": stat.correct_rate,
        }
        for result in state.results
        if (stat := result.run_stats.get(case.id)) is not None
    }
    return case.to_dict() | {"predictions": predictions}


def labels_payload() -> Payload:
    return {
        "labels": [
            {"value": item.label.value, "title": item.title, "mechanism": item.mechanism}
            for item in CATALOG
        ]
    }


def calibration_payload(state: AppState) -> Payload:
    if state.calibration is None:
        return {"available": False}
    return {"available": True} | state.calibration.to_dict()


# --- routing (pure) -------------------------------------------------------------------


def _error(status: HTTPStatus, message: str) -> Response:
    return status, {"error": message}


def handle_get(state: AppState, path: str, query: Mapping[str, str], user: str | None) -> Response:
    if path == "/api/health":
        return HTTPStatus.OK, {"status": "ok", "version": __version__}
    if path == "/api/session":
        return HTTPStatus.OK, {"authenticated": user is not None, "user": user}
    if user is None:
        return _error(HTTPStatus.UNAUTHORIZED, "authentication required")
    try:
        if path == "/api/overview":
            return HTTPStatus.OK, overview_payload(state)
        if path == "/api/labels":
            return HTTPStatus.OK, labels_payload()
        if path == "/api/calibration":
            return HTTPStatus.OK, calibration_payload(state)
        if path == "/api/candidate":
            found = candidate_payload(state, query.get("name", ""))
            return (
                (HTTPStatus.OK, found)
                if found
                else _error(HTTPStatus.NOT_FOUND, "unknown candidate")
            )
        if path == "/api/cases":
            return HTTPStatus.OK, cases_payload(state, query)
        if path == "/api/case":
            found = case_payload(state, query.get("id", ""))
            return (HTTPStatus.OK, found) if found else _error(HTTPStatus.NOT_FOUND, "unknown case")
        if path == "/api/whatif":
            split = query.get("split", "hard")
            if split not in EVAL_SPLITS:
                raise BadRequestError("unknown split")
            margin = parse_margin(query.get("margin", "0.03"))
            alpha = parse_choice(query.get("alpha", "0.05"), ALPHAS, "alpha")
            size = int(parse_choice(query.get("n", "0"), SIZES, "n"))
            found = whatif_payload(state, query.get("name", ""), split, margin, alpha, size)
            return (
                (HTTPStatus.OK, found)
                if found
                else _error(HTTPStatus.NOT_FOUND, "no paired reference")
            )
    except BadRequestError as error:
        return _error(HTTPStatus.BAD_REQUEST, str(error))
    return _error(HTTPStatus.NOT_FOUND, "unknown endpoint")


def handle_post(
    state: AppState, path: str, body: Mapping[str, Any], client: str
) -> tuple[HTTPStatus, Payload, str | None]:
    """Third element: session token to set, "" to clear, None to leave alone."""
    if path == "/api/login":
        if not state.throttle.allow(client):
            return HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many attempts, wait a minute"}, None
        user = str(body.get("user", ""))[:128]
        password = str(body.get("password", ""))[:256]
        if not credentials_ok(state.credentials, user, password):
            return HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"}, None
        # Signed over the configured user, not over the string from the request: nothing
        # that came from the network reaches a Set-Cookie header.
        token = make_session(state.secret, state.credentials.user)
        return HTTPStatus.OK, {"authenticated": True, "user": state.credentials.user}, token
    if path == "/api/logout":
        return HTTPStatus.OK, {"authenticated": False}, ""
    return HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"}, None


# --- HTTP -----------------------------------------------------------------------------

_HTML = "text/html; charset=utf-8"
# A fixed set of files, not a directory to serve: no path is built from network input,
# so there is no traversal to guard against.
_STATIC: dict[str, tuple[str, str]] = {
    "/": ("index.html", _HTML),
    "/index.html": ("index.html", _HTML),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def make_handler(state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"llm-eval-gate/{__version__}"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write(f"{self.address_string()} {format % args}\n")

        def _https(self) -> bool:
            return state.trust_proxy and self.headers.get("X-Forwarded-Proto", "") == "https"

        def _client(self) -> str:
            if state.trust_proxy:
                forwarded = self.headers.get("X-Forwarded-For", "")
                if forwarded:
                    return forwarded.split(",")[0].strip()[:64]
            return str(self.client_address[0])

        def _user(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE and value:
                    return read_session(state.secret, value)
            return None

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            cookie: str | None = None,
            cache: str = "no-store",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for header, value in SECURITY_HEADERS.items():
                self.send_header(header, value)
            if self._https():
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            if cookie is not None:
                secure = "; Secure" if self._https() else ""
                max_age = SESSION_TTL_SECONDS if cookie else 0
                attributes = f"Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; {attributes}")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: HTTPStatus, payload: Payload, cookie: str | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", cookie=cookie)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in _STATIC:
                name, content_type = _STATIC[parsed.path]
                self._send(
                    HTTPStatus.OK, (WEB_ROOT / name).read_bytes(), content_type, cache="no-cache"
                )
                return
            if not parsed.path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            query = {
                k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items() if v
            }
            status, payload = handle_get(state, parsed.path, query, self._user())
            self._json(status, payload)

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self.close_connection = True
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
                return
            # The bounded body is read before any refusal: answering with unread bytes in
            # the socket makes the close a reset, and the client never sees the answer.
            raw = self.rfile.read(length) if length else b""
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json required"}
                )
                return
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request refused"})
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if not isinstance(body, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON object required"})
                return
            status, payload, cookie = handle_post(state, parsed.path, body, self._client())
            self._json(status, payload, cookie)

    return Handler


def make_server(state: AppState, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(state))
    server.daemon_threads = True
    return server


def serve(project: Project, *, host: str, port: int) -> None:
    state = build_state(project)
    server = make_server(state, host, port)
    print(f"llm-eval-gate dashboard em http://{host}:{server.server_address[1]}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        pass
    finally:
        server.server_close()
