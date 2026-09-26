"""JSON over HTTP for providers: stdlib only, bounded in time and in size.

The opener is built by hand with the HTTP and HTTPS handlers and nothing else. The default
`urllib` opener also speaks `file://`, `ftp://` and `data:`, and follows redirects; a
provider URL from configuration has no business doing any of that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from llm_eval_gate import __version__
from llm_eval_gate.providers.base import ProviderError

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_MAX_BYTES = 2_000_000

Transport = Callable[[str, Mapping[str, Any], Mapping[str, str], float], dict[str, Any]]


def _build_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_OPENER = _build_opener()


def validate_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"provider URL must be http(s)://host[:port], got {url!r}")
    if parsed.username or parsed.password:
        raise ValueError("provider URL must not embed credentials; use an environment variable")
    return url.rstrip("/")


def _where(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_s: float,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    validate_base_url(url)
    request = urllib.request.Request(  # noqa: S310 - scheme validated above
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"llm-eval-gate/{__version__}",
            **headers,
        },
    )
    try:
        with _OPENER.open(request, timeout=timeout_s) as response:
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        # Header values (an API key) never reach the message.
        transient = error.code >= 500 or error.code == 429
        raise ProviderError(f"HTTP {error.code} from {_where(url)}", transient=transient) from None
    except (urllib.error.URLError, OSError) as error:
        raise ProviderError(
            f"cannot reach {_where(url)}: {error.__class__.__name__}", transient=True
        ) from None
    if len(body) > max_bytes:
        raise ProviderError(
            f"response from {_where(url)} exceeds {max_bytes} bytes", transient=False
        )
    try:
        value = json.loads(body)
    except ValueError:
        raise ProviderError(f"response from {_where(url)} is not JSON", transient=False) from None
    if not isinstance(value, dict):
        raise ProviderError(f"response from {_where(url)} is not a JSON object", transient=False)
    return value
