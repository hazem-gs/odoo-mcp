"""MCP tool surface for the Odoo task/timesheet domain.

Built on the installed SDK's high-level ``MCPServer`` class
(``from mcp.server.mcpserver import MCPServer``; verified against the pinned
``mcp`` 2.0.0 install - the legacy ``FastMCP`` name does not exist there).

Exactly nine tools are exposed; there is deliberately no generic model/method
passthrough. Every tool is stateless: it resolves credentials from the
environment via ``OdooConfig.from_env()`` and authenticates per call,
mirroring the CLI's semantics. Domain errors (``ConfigError``,
``OdooRpcError``, ``ValueError``) propagate out of the tool functions; the
SDK converts them into readable tool errors on the wire.

Nothing in this module writes to stdout - stdout is the stdio transport.
"""

import datetime
import math

from mcp.server.mcpserver import MCPServer

from odoo_mcp import rpc, tasks, timesheets
from odoo_mcp.config import OdooConfig

mcp = MCPServer("odoo")


def _connect() -> tuple[str, str, int, str]:
    """Resolve env config and authenticate; returns (url, db, uid, password)."""
    cfg = OdooConfig.from_env()
    uid = rpc.authenticate(cfg.url, cfg.db, cfg.username, cfg.password)
    return cfg.url, cfg.db, uid, cfg.password


def tool_names() -> list[str]:
    """Registered tool names (introspection helper for tests/docs).

    Reads the sync registry (``mcp._tool_manager.list_tools()``); the public
    ``mcp.list_tools()`` is a coroutine and unusable from sync contexts.
    """
    return sorted(tool.name for tool in mcp._tool_manager.list_tools())


@mcp.tool()
async def list_my_tasks(
    include_done: bool = False,
    limit: int = 0,
    project_id: int | None = None,
    stage_id: int | None = None,
    state: str | None = None,
    due_after: str | None = None,
    due_before: str | None = None,
) -> list[dict]:
    """List the caller's Odoo tasks (tasks they are assigned to).

    By default terminal states (done/cancelled/closed) are excluded; pass
    include_done=true to see everything. Optional filters: project_id,
    stage_id, exact state code, deadline window due_after/due_before
    (YYYY-MM-DD). limit=0 means no limit.
    """
    url, db, uid, password = _connect()
    selection = tasks.fetch_state_selection(url, db, uid, password)
    filters = tasks.TaskFilters(
        project_id=project_id,
        stage_id=stage_id,
        state=state,
        due_after=due_after,
        due_before=due_before,
    )
    return tasks.fetch_my_tasks(
        url, db, uid, password,
        include_done, limit, tasks.terminal_state_codes(selection), filters,
    )


@mcp.tool()
async def get_task(task_id: int) -> dict:
    """Fetch one task by id with full detail fields (hours, progress, stage...)."""
    url, db, uid, password = _connect()
    task = tasks.fetch_task(url, db, uid, password, task_id, tasks.SHOW_FIELDS)
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    return task


@mcp.tool()
async def update_task(
    task_id: int,
    name: str | None = None,
    description: str | None = None,
    append_description: str | None = None,
    allocated_hours: float | None = None,
    progress: float | None = None,
    priority: int | None = None,
    deadline: str | None = None,
    state: str | None = None,
    stage: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Update mutable fields of a task; executes immediately unless dry_run.

    Field args: name, description (replaces), append_description (appends to
    the existing description after a blank line), allocated_hours (planned
    effort), progress (0-100; computed field - prefer allocated_hours),
    priority (0 low .. 3 urgent), deadline (YYYY-MM-DD), state (workflow code
    from get_task_states), stage (exact stage display name). With dry_run=true
    returns {"dry_run": true, "vals": {...}} without writing.
    """
    if all(
        v is None
        for v in (
            name, description, append_description, progress,
            allocated_hours, priority, deadline, state, stage,
        )
    ):
        raise ValueError("Nothing to update - pass at least one field.")
    url, db, uid, password = _connect()
    if state is not None:
        valid = [code for code, _ in tasks.fetch_state_selection(url, db, uid, password)]
        if state not in valid:
            raise ValueError(
                f"Unknown state {state!r}. Valid states on this server: "
                + ", ".join(valid)
            )
    vals = tasks.validate_update_vals(
        name=name,
        description=description,
        progress=progress,
        allocated_hours=allocated_hours,
        priority=priority,
        deadline=deadline,
        state=state,
    )
    if stage is not None:
        vals["stage_id"] = tasks.find_stage_id(url, db, uid, password, stage)
    if append_description is not None:
        current = tasks.fetch_task(url, db, uid, password, task_id, ["description"])
        base = (current or {}).get("description") or ""
        vals["description"] = base.rstrip() + "\n\n" + append_description
    if dry_run:
        return {"dry_run": True, "vals": vals}
    tasks.update_task(url, db, uid, password, task_id, vals)
    updated = tasks.fetch_task(url, db, uid, password, task_id, tasks.SHOW_FIELDS)
    if updated is None:
        raise rpc.OdooRpcError(f"Task {task_id} not found or no read access.")
    return updated


@mcp.tool()
async def post_task_message(task_id: int, message: str, dry_run: bool = False) -> dict:
    """Post a message to a task's chatter (followers get notified).

    Returns {"message_id": id}, or {"dry_run": true, ...} with dry_run=true.
    """
    if not message.strip():
        raise ValueError("Nothing to post - pass a non-empty message.")
    if dry_run:
        return {"dry_run": True, "task_id": task_id, "message": message}
    url, db, uid, password = _connect()
    return {"message_id": tasks.post_message(url, db, uid, password, task_id, message)}


@mcp.tool()
async def get_task_states() -> list[dict]:
    """List this server's valid task workflow states as {code, label, terminal}."""
    url, db, uid, password = _connect()
    selection = tasks.fetch_state_selection(url, db, uid, password)
    terminal = set(tasks.terminal_state_codes(selection))
    return [
        {"code": code, "label": label, "terminal": code in terminal}
        for code, label in selection
    ]


@mcp.tool()
async def list_stages() -> list[dict]:
    """List the task stages (project.task.type) available on the server."""
    url, db, uid, password = _connect()
    return rpc.execute_kw(
        url, db, uid, password,
        "project.task.type", "search_read",
        [[]], {"fields": ["id", "name"]},
    )


@mcp.tool()
async def list_timesheets(
    task_id: int | None = None, days: int | None = None, limit: int = 50
) -> list[dict]:
    """List the caller's timesheet entries, newest first.

    Optional filters: task_id restricts to one task; days restricts to the
    last N days; limit caps the row count (default 50).
    """
    url, db, uid, password = _connect()
    return timesheets.fetch_timesheets(
        url, db, uid, password, task_id=task_id, days=days, limit=limit
    )


@mcp.tool()
async def create_timesheet(
    task_id: int,
    hours: float,
    date: str | None = None,
    description: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Log hours worked on a task (defaults to today); returns {"entry_id": id}.

    hours must be > 0; date must be YYYY-MM-DD. With dry_run=true returns
    {"dry_run": true, "vals": {...}} without writing.
    """
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("Hours must be greater than 0.")
    entry_date = (
        tasks._validate_date(date)
        if date is not None
        else datetime.date.today().isoformat()
    )
    if dry_run:
        vals: dict = {"task_id": task_id, "unit_amount": hours, "date": entry_date}
        if description:
            vals["name"] = description
        return {"dry_run": True, "vals": vals}
    url, db, uid, password = _connect()
    entry_id = timesheets.create_timesheet(
        url, db, uid, password, task_id, hours,
        date=date, description=description,
    )
    return {"entry_id": entry_id}


@mcp.tool()
async def update_timesheet(
    entry_id: int,
    hours: float | None = None,
    date: str | None = None,
    description: str | None = None,
    task_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Modify an existing timesheet entry; returns {"ok": true} on success.

    Pass at least one of hours (> 0), date (YYYY-MM-DD), description or
    task_id (moves the entry, keeping its project consistent). With
    dry_run=true returns {"dry_run": true, "vals": {...}} without writing.
    """
    vals: dict = {}
    if hours is not None:
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError("Hours must be greater than 0.")
        vals["unit_amount"] = hours
    if date is not None:
        vals["date"] = tasks._validate_date(date)
    if description is not None:
        vals["name"] = description
    if task_id is not None:
        vals["task_id"] = task_id
    if not vals:
        raise ValueError(
            "Nothing to update - pass hours, date, description or task_id."
        )
    if dry_run:
        return {"dry_run": True, "vals": vals}
    url, db, uid, password = _connect()
    timesheets.update_timesheet(
        url, db, uid, password, entry_id,
        hours=hours, date=date, description=description, task_id=task_id,
    )
    return {"ok": True}
