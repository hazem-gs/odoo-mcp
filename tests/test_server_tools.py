"""In-memory MCP protocol tests for the odoo tool surface (plan todo T10).

Client mechanism (recorded per plan requirement): the installed ``mcp``
2.0.0 has NO ``create_connected_server_and_client_session`` helper, so these
tests pair ``mcp.client.session.ClientSession`` with the server's low-level
app (``odoo_mcp.server.mcp._lowlevel_server``) over anyio memory streams
from ``mcp.shared.memory.create_client_server_memory_streams`` - exactly the
wiring ``MCPServer.run_stdio_async`` performs, minus the stdio transport.

Seam strategy: configuration comes from the real ``OdooConfig`` reading
monkeypatched environment variables; authentication is faked at
``odoo_mcp.rpc.authenticate``; ONE scripted ``execute_kw`` fake is installed
on every namespace that bound it at import time (``odoo_mcp.rpc``,
``odoo_mcp.tasks``, ``odoo_mcp.timesheets``, ``odoo_mcp.server``). No test
here touches the network.
"""

import json

import anyio
import pytest
from contextlib import asynccontextmanager
from mcp.client.session import ClientSession
from mcp.shared import memory as shared_memory

from odoo_mcp import rpc as rpc_mod
from odoo_mcp import server as server_mod
from odoo_mcp import tasks as tasks_mod
from odoo_mcp import timesheets as timesheets_mod

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

STATE_SELECTION = [
    ["01_in_progress", "In Progress"],
    ["1_done", "Done"],
    ["04_cancelled", "Cancelled"],
]

TASK_ROW = {
    "id": 252,
    "name": "Fix the flux capacitor",
    "state": "01_in_progress",
    "priority": 1,
    "progress": 50.0,
    "description": "existing notes",
    "date_deadline": "2026-09-01",
    "stage_id": [3, "In Progress"],
    "project_id": [1, "Internal"],
    "user_ids": [2],
    "create_date": "2026-08-01 10:00:00",
    "allocated_hours": 16,
    "effective_hours": 8,
    "subtask_effective_hours": 0,
    "parent_id": False,
    "write_date": "2026-08-20 09:00:00",
}

TIMESHEET_ROW = {
    "task_id": [252, "Fix the flux capacitor"],
    "project_id": [1, "Internal"],
    "date": "2026-08-21",
    "unit_amount": 1.5,
    "name": "wiring",
    "user_id": [2, "mcp-tester"],
    "employee_id": [7, "Emp Seven"],
}


class FakeExecuteKw:
    """Scripted stand-in for Odoo ``execute_kw``, dispatched by (model, method)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list, dict | None]] = []
        self._routes: dict[tuple[str, str], object] = {}

    def route(self, model: str, method: str, handler: object) -> None:
        self._routes[(model, method)] = handler

    def __call__(
        self,
        url: str,
        db: str,
        uid: int,
        password: str,
        model: str,
        method: str,
        args: list,
        kwargs: dict | None = None,
    ):
        self.calls.append((model, method, args, kwargs))
        try:
            handler = self._routes[(model, method)]
        except KeyError:
            raise AssertionError(f"Unexpected RPC call {model}.{method}") from None
        return handler(args, kwargs) if callable(handler) else handler

    def writes(self, model: str, method: str) -> list[tuple[list, dict | None]]:
        """Recorded (args, kwargs) for one model/method - e.g. asserted writes."""
        return [(a, k) for m, meth, a, k in self.calls if (m, meth) == (model, method)]


def task_search_read_handler(rows_by_id: dict[int, dict]):
    """search_read handler: exact-id domains hit rows_by_id, others list all."""

    def handle(args: list, kwargs: dict | None):
        domain = args[0]
        wanted = [
            leaf[2]
            for leaf in domain
            if isinstance(leaf, list)
            and len(leaf) == 3
            and leaf[0] == "id"
            and leaf[1] == "="
        ]
        if wanted:
            row = rows_by_id.get(wanted[0])
            return [dict(row)] if row else []
        return [dict(r) for r in rows_by_id.values()]

    return handle


@pytest.fixture
def fake_rpc(monkeypatch: pytest.MonkeyPatch) -> FakeExecuteKw:
    """Install one scripted execute_kw on every namespace that imported it."""
    fake = FakeExecuteKw()
    fake.route("project.task", "fields_get", {"state": {"selection": STATE_SELECTION}})
    fake.route(
        "project.task", "search_read", task_search_read_handler({252: TASK_ROW})
    )
    fake.route("project.task", "write", True)
    fake.route("project.task", "message_post", 555)
    fake.route("project.task.type", "search_read", [{"id": 3, "name": "In Progress"}])
    fake.route("res.users", "read", [{"employee_id": [7, "Emp Seven"]}])
    fake.route("account.analytic.line", "create", 901)
    fake.route("account.analytic.line", "write", True)
    fake.route("account.analytic.line", "search_read", [TIMESHEET_ROW])
    for mod in (rpc_mod, tasks_mod, timesheets_mod):
        monkeypatch.setattr(mod, "execute_kw", fake)
    return fake


@pytest.fixture(autouse=True)
def odoo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dummy credentials via env; url/db left to fall back to defaults."""
    monkeypatch.setenv("ODOO_USERNAME", "mcp-tester@example.com")
    monkeypatch.setenv("ODOO_PASSWORD", "dummy-secret")
    monkeypatch.delenv("ODOO_URL", raising=False)
    monkeypatch.delenv("ODOO_DB", raising=False)


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpc_mod, "authenticate", lambda *args: 2)


@asynccontextmanager
async def mcp_client():
    """Yield a ClientSession wired to the package server over memory streams."""
    lowlevel = server_mod.mcp._lowlevel_server
    async with shared_memory.create_client_server_memory_streams() as (
        client_streams,
        server_streams,
    ):
        init_options = lowlevel.create_initialization_options()
        async with ClientSession(*client_streams, read_timeout_seconds=30) as client:
            async with anyio.create_task_group() as tg:
                tg.start_soon(lowlevel.run, server_streams[0], server_streams[1], init_options)
                await client.initialize()
                yield client
                tg.cancel_scope.cancel()


async def call(client: ClientSession, name: str, arguments: dict):
    return await client.call_tool(name, arguments)


def payload(result) -> object:
    """Unwrap a successful CallToolResult into its Python payload.

    Tools annotated ``-> dict`` get no SDK structured output (only text), so
    fall back to parsing the JSON text content when needed.
    """
    assert not result.is_error, f"tool failed: {result.content[0].text}"
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


def error_text(result) -> str:
    assert result.is_error, "expected the tool call to fail"
    return result.content[0].text


# --- registration ---------------------------------------------------------


@pytest.mark.anyio
async def test_server_exposes_exactly_the_nine_documented_tools():
    async with mcp_client() as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
    assert names == EXPECTED_TOOLS
    assert len(names) == 9


# --- list_my_tasks --------------------------------------------------------


@pytest.mark.anyio
async def test_list_my_tasks_returns_rows_and_forwards_limit(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "list_my_tasks", {"limit": 5})
    assert payload(result) == [TASK_ROW]
    (_, _, args, kwargs) = fake_rpc.calls[-1]
    assert kwargs["limit"] == 5
    assert kwargs["fields"] == tasks_mod.TASK_FIELDS


@pytest.mark.anyio
async def test_list_my_tasks_default_resolves_terminal_states_and_omits_limit(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "list_my_tasks", {})
    assert payload(result) == [TASK_ROW]
    models_methods = [(m, meth) for m, meth, _, _ in fake_rpc.calls]
    assert ("project.task", "fields_get") in models_methods
    (_, _, _, kwargs) = fake_rpc.calls[-1]
    assert "limit" not in kwargs


# --- get_task -------------------------------------------------------------


@pytest.mark.anyio
async def test_get_task_returns_full_detail_row(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "get_task", {"task_id": 252})
    assert payload(result) == TASK_ROW
    (_, _, args, kwargs) = fake_rpc.calls[-1]
    assert kwargs["fields"] == tasks_mod.SHOW_FIELDS


@pytest.mark.anyio
async def test_get_task_missing_reports_not_found(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "get_task", {"task_id": 999999})
    assert "Task 999999 not found" in error_text(result)


# --- update_task ------------------------------------------------------------


@pytest.mark.anyio
async def test_update_task_executes_write_and_returns_updated_row(fake_rpc):
    updated = dict(TASK_ROW, allocated_hours=20)
    fake_rpc.route(
        "project.task", "search_read", task_search_read_handler({252: updated})
    )
    async with mcp_client() as client:
        result = await call(
            client, "update_task", {"task_id": 252, "allocated_hours": 20}
        )
    assert payload(result) == updated
    writes = fake_rpc.writes("project.task", "write")
    assert writes == [([[252], {"allocated_hours": 20.0}], None)]


@pytest.mark.anyio
async def test_update_task_dry_run_previews_without_writing(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "update_task", {"task_id": 252, "priority": 2, "dry_run": True}
        )
    assert payload(result) == {"dry_run": True, "vals": {"priority": 2}}
    assert fake_rpc.writes("project.task", "write") == []


@pytest.mark.anyio
async def test_update_task_unknown_state_error_lists_valid_states(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "update_task", {"task_id": 252, "state": "bogus"})
    text = error_text(result)
    assert "Unknown state 'bogus'" in text
    assert "Valid states on this server:" in text
    assert "01_in_progress" in text and "1_done" in text


@pytest.mark.anyio
async def test_update_task_resolves_stage_name_to_id(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "update_task", {"task_id": 252, "stage": "In Progress"}
        )
    assert not result.is_error
    assert fake_rpc.writes("project.task", "write") == [
        ([[252], {"stage_id": 3}], None)
    ]


@pytest.mark.anyio
async def test_update_task_append_description_merges_after_blank_line(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client,
            "update_task",
            {"task_id": 252, "append_description": "new bit"},
        )
    assert not result.is_error
    assert fake_rpc.writes("project.task", "write") == [
        ([[252], {"description": "existing notes\n\nnew bit"}], None)
    ]


# --- post_task_message ------------------------------------------------------


@pytest.mark.anyio
async def test_post_task_message_returns_message_id(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "post_task_message", {"task_id": 252, "message": "status: on track"}
        )
    assert payload(result) == {"message_id": 555}
    assert fake_rpc.writes("project.task", "message_post") == [
        ([[252]], {"body": "status: on track"})
    ]


@pytest.mark.anyio
async def test_post_task_message_rejects_whitespace_only_message(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "post_task_message", {"task_id": 252, "message": "   \n\t "}
        )
    assert "Nothing to post" in error_text(result)
    assert fake_rpc.writes("project.task", "message_post") == []


# --- get_task_states / list_stages -------------------------------------------


@pytest.mark.anyio
async def test_get_task_states_marks_terminal_states(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "get_task_states", {})
    states = {row["code"]: row for row in payload(result)}
    assert states["01_in_progress"]["terminal"] is False
    assert states["01_in_progress"]["label"] == "In Progress"
    assert states["1_done"]["terminal"] is True
    assert states["04_cancelled"]["terminal"] is True


@pytest.mark.anyio
async def test_list_stages_search_reads_task_types(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "list_stages", {})
    assert payload(result) == [{"id": 3, "name": "In Progress"}]
    assert fake_rpc.calls[-1][:2] == ("project.task.type", "search_read")


# --- timesheets ---------------------------------------------------------------


@pytest.mark.anyio
async def test_list_timesheets_forwards_filters_and_limit(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "list_timesheets", {"task_id": 252, "days": 7, "limit": 10}
        )
    assert payload(result) == [TIMESHEET_ROW]
    (_, _, args, kwargs) = fake_rpc.calls[-1]
    assert ["task_id", "=", 252] in args[0]
    assert kwargs["limit"] == 10
    assert kwargs["fields"] == timesheets_mod.TIMESHEET_FIELDS


@pytest.mark.anyio
async def test_create_timesheet_returns_entry_id(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client,
            "create_timesheet",
            {
                "task_id": 252,
                "hours": 1.5,
                "date": "2026-08-21",
                "description": "wiring",
            },
        )
    assert payload(result) == {"entry_id": 901}
    (args, _) = fake_rpc.writes("account.analytic.line", "create")[0]
    vals = args[0]
    assert vals["task_id"] == 252
    assert vals["unit_amount"] == 1.5
    assert vals["date"] == "2026-08-21"
    assert vals["name"] == "wiring"


@pytest.mark.anyio
async def test_create_timesheet_rejects_nonpositive_hours(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "create_timesheet", {"task_id": 252, "hours": 0})
    assert "Hours must be greater than 0." in error_text(result)
    assert fake_rpc.writes("account.analytic.line", "create") == []


@pytest.mark.anyio
async def test_create_timesheet_rejects_invalid_date(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client,
            "create_timesheet",
            {"task_id": 252, "hours": 1, "date": "2026-02-30"},
        )
    assert "Date must be a valid YYYY-MM-DD calendar date." in error_text(result)


@pytest.mark.anyio
async def test_update_timesheet_writes_provided_fields(fake_rpc):
    async with mcp_client() as client:
        result = await call(
            client, "update_timesheet", {"entry_id": 9, "hours": 2}
        )
    assert payload(result) == {"ok": True}
    assert fake_rpc.writes("account.analytic.line", "write") == [
        ([[9], {"unit_amount": 2.0}], None)
    ]


@pytest.mark.anyio
async def test_update_timesheet_requires_at_least_one_field(fake_rpc):
    async with mcp_client() as client:
        result = await call(client, "update_timesheet", {"entry_id": 9})
    assert "Nothing to update" in error_text(result)
    assert fake_rpc.writes("account.analytic.line", "write") == []


# --- credentials ----------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_credentials_raise_config_error_naming_vars_without_values(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ODOO_USERNAME", raising=False)
    monkeypatch.delenv("ODOO_PASSWORD", raising=False)
    async with mcp_client() as client:
        result = await call(client, "get_task_states", {})
    text = error_text(result)
    assert "ODOO_USERNAME" in text
    assert "ODOO_PASSWORD" in text
    assert "mcp-tester@example.com" not in text
    assert "dummy-secret" not in text
