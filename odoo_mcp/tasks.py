"""Project-task domain operations for a self-hosted Odoo instance."""

import datetime
import math
from dataclasses import dataclass

from odoo_mcp.rpc import OdooRpcError, execute_kw

# Fields worth showing for a project task.
TASK_FIELDS = [
    "name",
    "state",
    "priority",
    "progress",
    "description",
    "date_deadline",
    "stage_id",
    "project_id",
    "user_ids",
    "create_date",
]

# Extra fields for the `show` command. Hours drive the computed `progress`
# field (progress = logged hours / planned hours), so surface them together.
SHOW_FIELDS = TASK_FIELDS + [
    "allocated_hours",
    "effective_hours",
    "subtask_effective_hours",
    "parent_id",
    "write_date",
]

# Odoo priority: 0 Low, 1 Normal, 2 High, 3 Urgent
PRIORITY_LABELS = {0: "low", 1: "normal", 2: "high", 3: "urgent"}

# Workflow states are NOT hardcoded: they vary per Odoo build (this instance
# uses '1_done' and '04_waiting_normal', not '04_done'). Valid state codes are
# read from the server at runtime via fetch_state_selection().


@dataclass(frozen=True, slots=True)
class TaskFilters:
    project_id: int | None
    stage_id: int | None
    state: str | None
    due_after: str | None
    due_before: str | None


def _validate_date(value: str) -> str:
    value = value.strip()
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise ValueError("Date must be a valid YYYY-MM-DD calendar date.") from e


def validate_update_vals(
    *,
    name=None,
    description=None,
    progress=None,
    allocated_hours=None,
    priority=None,
    deadline=None,
    state=None,
) -> dict:
    """Collect updatable task fields into an Odoo ``vals`` dict.

    Raises ValueError on invalid input; identical rules/messages to the
    former CLI-only ``build_update_vals``.
    """
    vals: dict = {}
    if name is not None:
        vals["name"] = name.strip()
    if description is not None:
        vals["description"] = description
    if progress is not None:
        if not math.isfinite(progress) or not 0 <= progress <= 100:
            raise ValueError("Progress must be between 0 and 100.")
        vals["progress"] = progress
    if allocated_hours is not None:
        if not math.isfinite(allocated_hours) or allocated_hours < 0:
            raise ValueError("Allocated hours must be >= 0.")
        vals["allocated_hours"] = allocated_hours
    if priority is not None:
        vals["priority"] = priority
    if deadline is not None:
        vals["date_deadline"] = _validate_date(deadline)
    if state is not None:
        vals["state"] = state
    return vals


def fetch_my_tasks(
    url: str,
    db: str,
    uid: int,
    password: str,
    include_done: bool,
    limit: int,
    terminal_states: list[str],
    filters: TaskFilters,
) -> list[dict]:
    """Return tasks where the current user is an assignee."""
    domain = [["user_ids", "in", uid]]
    if not include_done:
        domain.append(["state", "not in", terminal_states])
    if filters.project_id is not None:
        domain.append(["project_id", "=", filters.project_id])
    if filters.stage_id is not None:
        domain.append(["stage_id", "=", filters.stage_id])
    if filters.state is not None:
        domain.append(["state", "=", filters.state])
    if filters.due_after is not None:
        domain.append(["date_deadline", ">=", filters.due_after])
    if filters.due_before is not None:
        domain.append(["date_deadline", "<=", filters.due_before])

    kwargs = {
        "fields": TASK_FIELDS,
        "order": "priority desc, date_deadline",
    }
    if limit:
        kwargs["limit"] = limit

    return execute_kw(
        url, db, uid, password, "project.task", "search_read", [domain], kwargs
    )


def update_task(
    url: str, db: str, uid: int, password: str, task_id: int, vals: dict
) -> bool:
    """Write *vals* to task *task_id*; the server raises on access/validation errors."""
    result = execute_kw(
        url, db, uid, password, "project.task", "write", [[task_id], vals]
    )
    if result is not True:
        raise OdooRpcError(
            f"Write to task {task_id} failed (server returned {result!r})."
        )
    return True


def find_stage_id(url: str, db: str, uid: int, password: str, name: str) -> int:
    """Resolve a task stage by its exact display name."""
    stages = execute_kw(
        url,
        db,
        uid,
        password,
        "project.task.type",
        "search_read",
        [[["name", "=", name]]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not stages:
        raise OdooRpcError(f"No task stage named {name!r} found.")
    return stages[0]["id"]


def fetch_state_selection(url: str, db: str, uid: int, password: str) -> list:
    """Return the valid [(value, label)] choices for the task 'state' field."""
    try:
        fields = execute_kw(
            url, db, uid, password, "project.task", "fields_get",
            [], {"attributes": ["selection"]},
        )
        selection = fields.get("state", {}).get("selection") or []
        out = []
        for item in selection:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((str(item[0]), str(item[1])))
            elif isinstance(item, str):
                out.append((item, item))
        if out:
            return out
    except OdooRpcError:
        pass
    rows = execute_kw(
        url, db, uid, password, "project.task", "search_read",
        [[]], {"fields": ["state"], "limit": 1000},
    )
    states = list(dict.fromkeys(r.get("state") for r in rows if r.get("state")))
    return [(s, s) for s in states]


def terminal_state_codes(selection: list[tuple[str, str]]) -> list[str]:
    markers = ("done", "cancelled", "canceled", "closed")
    return [
        code
        for code, label in selection
        if any(marker in f"{code} {label}".lower() for marker in markers)
    ]


def fetch_task(
    url: str, db: str, uid: int, password: str, task_id: int, fields: list | None = None
) -> dict | None:
    """Return a single task dict, or None if it does not exist / is unreadable."""
    tasks = execute_kw(
        url, db, uid, password, "project.task", "search_read",
        [[["id", "=", task_id]]], {"fields": fields or TASK_FIELDS, "limit": 1},
    )
    return tasks[0] if tasks else None


def post_message(
    url: str, db: str, uid: int, password: str, task_id: int, body: str
) -> int:
    """Post *body* to a task's chatter (followers get notified)."""
    result = execute_kw(
        url, db, uid, password, "project.task", "message_post",
        [[task_id]], {"body": body},
    )
    if not result:
        raise OdooRpcError(f"message_post on task {task_id} returned {result!r}.")
    return result
