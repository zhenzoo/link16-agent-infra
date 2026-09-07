"""Bounded HTTP for the official synchronous SDK registration flow.

Only the SDK registration module's requests binding is temporarily replaced.
The SDK still owns URLs, Device Grant state, polling and domain switching;
other requests users and installed SDK files are untouched.
"""
from contextlib import contextmanager
from importlib.metadata import version
import inspect
import time
from urllib.parse import parse_qs

import lark_oapi as lark
import lark_oapi.scene.registration as sdk
import requests


class RegistrationTransportError(RuntimeError):
    pass


def preflight():
    installed = version("lark_oapi")
    required = {"on_qr_code", "on_status_change", "app_preset", "addons", "create_only", "app_id"}
    if not required.issubset(inspect.signature(lark.register_app).parameters):
        raise RegistrationTransportError(
            "SDK incompatible: install lark_oapi>=1.7.3 before registration"
        )
    if not hasattr(sdk, "requests") or not hasattr(sdk, "_SyncFlow"):
        raise RegistrationTransportError("SDK registration transport changed; review adapter")
    return installed


def safe_error(exc):
    # SDK and requests exception messages may include response bodies or URLs.
    if isinstance(exc, RegistrationTransportError):
        return str(exc)
    return type(exc).__name__


class RegistrationHTTP:
    def __init__(self, report, post=None, sleep=None):
        self.report = report
        self._post = post or requests.post
        self._sleep = sleep or time.sleep
        self.deadline = None

    def post(self, url, **kwargs):
        action = parse_qs(kwargs.get("data", "")).get("action", [""])[0]
        if action not in {"init", "begin", "poll"}:
            raise RegistrationTransportError("SDK request action changed; review adapter")
        kwargs["timeout"] = (10, 20)
        attempts = 3 if action == "poll" else 1
        for attempt in range(1, attempts + 1):
            if action == "poll" and self.deadline is not None and time.monotonic() >= self.deadline:
                raise RegistrationTransportError("poll:expired_token")
            self.report(phase=action, outcome="request", attempt=attempt)
            error = None
            response = None
            try:
                response = self._post(url, **kwargs)
                status = response.status_code
                self.report(phase=action, outcome="response", http_status=status)
                if status in {429, 500, 502, 503, 504}:
                    error = f"HTTP_{status}"
                else:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        error = "InvalidResponse"
                    elif status >= 400 and "error" not in payload:
                        raise RegistrationTransportError(f"{action}:HTTP_{status}")
                    else:
                        if action == "begin" and isinstance(payload.get("expires_in"), (int, float)):
                            self.deadline = time.monotonic() + payload["expires_in"]
                        return response
            except (requests.Timeout, requests.ConnectionError, ValueError) as exc:
                error = "InvalidJSON" if isinstance(exc, ValueError) else type(exc).__name__
            finally:
                if response is not None:
                    response.close()
            self.report(phase=action, outcome="transient_error", error_kind=error)
            if attempt == attempts:
                raise RegistrationTransportError(f"{action}:{error}; attempts={attempt}")
            self.report(phase=action, outcome="retry", attempt=attempt)
            self._sleep(2 ** attempt)


@contextmanager
def bounded_http(report):
    original = sdk.requests
    sdk.requests = RegistrationHTTP(report)
    try:
        yield
    finally:
        sdk.requests = original
