"""JSON-RPC transport for a self-hosted Odoo instance (stdlib only)."""

import json
import urllib.error
import urllib.request


class OdooRpcError(RuntimeError):
    """Raised when the Odoo server returns an error."""


def json_rpc(base_url: str, payload: dict) -> object:
    """POST a JSON-RPC 2.0 request to *base_url*/jsonrpc and return the result."""
    url = base_url.rstrip("/") + "/jsonrpc"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # Odoo reports API errors (bad login, invalid db, etc.) as HTTP 400
        # with the real reason in the JSON-RPC error body - extract it.
        body = e.read().decode("utf-8", errors="replace")
        detail = _extract_error_message(body)
        raise OdooRpcError(f"HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise OdooRpcError(f"Connection error: {e.reason}") from e

    data = _parse_json(raw)
    if "error" in data:
        err = data["error"]
        msg = err.get("data", {}).get("message", err.get("message", str(err)))
        raise OdooRpcError(msg)
    return data.get("result")


def _extract_error_message(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    err = data.get("error", {})
    return err.get("data", {}).get("message", err.get("message", ""))


def _parse_json(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OdooRpcError(f"Invalid JSON response: {raw[:200]}") from e
    if not isinstance(data, dict):
        raise OdooRpcError(f"Unexpected response shape: {raw[:200]}")
    return data


def authenticate(url: str, db: str, username: str, password: str) -> int:
    """Authenticate and return the Odoo user id (uid)."""
    uid = json_rpc(
        url,
        {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 1,
            "params": {
                "service": "common",
                "method": "authenticate",
                "args": [db, username, password, {}],
            },
        },
    )
    if not isinstance(uid, int) or uid <= 0:
        raise OdooRpcError("Authentication failed - check username/password (or API key).")
    return uid


def execute_kw(
    url: str,
    db: str,
    uid: int,
    password: str,
    model: str,
    method: str,
    args: list,
    kwargs: dict | None = None,
) -> object:
    """Call an Odoo model method via execute_kw."""
    return json_rpc(
        url,
        {
            "jsonrpc": "2.0",
            "method": "call",
            "id": 2,
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [db, uid, password, model, method, args, kwargs or {}],
            },
        },
    )
