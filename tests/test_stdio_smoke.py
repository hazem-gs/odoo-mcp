"""Subprocess stdio smoke test: `python -m odoo_mcp` speaks MCP over stdio.

Spawns the real server process (no shell), performs the newline-delimited
JSON-RPC handshake (initialize -> initialized notification -> tools/list)
and asserts serverInfo.name plus the exact nine tool names. Reads run with a
30s timeout via a worker thread (pipes on Windows have no read timeouts).
The child is terminated in a finally block. No network, no real credentials.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_TOOLS = {
    "list_my_tasks",
    "get_task",
    "update_task",
    "post_task_message",
    "get_task_states",
    "list_stages",
    "list_timesheets",
    "create_timesheet",
    "update_timesheet",
}

READ_TIMEOUT_SECONDS = 30


def _send(proc: subprocess.Popen, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _recv(proc: subprocess.Popen) -> dict:
    """Read one newline-delimited JSON-RPC frame with a 30s timeout."""
    assert proc.stdout is not None

    def read_line() -> str:
        return proc.stdout.readline().decode("utf-8")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read_line)
        line = future.result(timeout=READ_TIMEOUT_SECONDS)
    if not line:
        raise AssertionError("server closed stdout before responding")
    return json.loads(line)


def test_stdio_handshake_and_tools_list():
    env = os.environ.copy()
    env["ODOO_USERNAME"] = "u@example.com"
    env["ODOO_PASSWORD"] = "dummy"

    proc = subprocess.Popen(
        [sys.executable, "-m", "odoo_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=PROJECT_ROOT,
        shell=False,
    )
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            },
        )
        init = _recv(proc)
        assert init["result"]["serverInfo"]["name"] == "odoo"

        # Notification: no response expected.
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_msg = None
        for _ in range(10):  # skip any interleaved frames; bounded
            msg = _recv(proc)
            if msg.get("id") == 2:
                tools_msg = msg
                break
        assert tools_msg is not None, "no response for tools/list"

        names = {tool["name"] for tool in tools_msg["result"]["tools"]}
        assert names == EXPECTED_TOOLS
        assert len(names) == 9
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
