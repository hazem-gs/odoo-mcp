"""Timesheet domain operations (account.analytic.line) for Odoo."""

import datetime

from odoo_mcp.rpc import OdooRpcError, execute_kw
from odoo_mcp.tasks import _validate_date

# Fields worth showing for a timesheet entry (account.analytic.line).
TIMESHEET_FIELDS = [
    "task_id",
    "project_id",
    "date",
    "unit_amount",
    "name",
    "user_id",
    "employee_id",
]


def task_project_id(url: str, db: str, uid: int, password: str, task_id: int) -> object:
    """Return the [id, name] project of a task, or False; raises if the task is missing."""
    tasks = execute_kw(
        url, db, uid, password, "project.task", "search_read",
        [[["id", "=", task_id]]], {"fields": ["project_id"], "limit": 1},
    )
    if not tasks:
        raise OdooRpcError(f"Task {task_id} not found.")
    return tasks[0].get("project_id") or False


def my_employee_id(url: str, db: str, uid: int, password: str) -> object:
    """Return the id of the employee linked to the current user, or False."""
    users = execute_kw(
        url, db, uid, password, "res.users", "read", [[uid]],
        {"fields": ["employee_id"]},
    )
    if not users:
        return False
    emp = users[0].get("employee_id")
    if isinstance(emp, (list, tuple)):
        return emp[0]
    return emp or False


def create_timesheet(
    url: str,
    db: str,
    uid: int,
    password: str,
    task_id: int,
    hours: float,
    date: str | None = None,
    description: str | None = None,
) -> int:
    """Log hours on a task; returns the new timesheet entry id."""
    if hours <= 0:
        raise ValueError("Hours must be greater than 0.")
    entry_date = (
        _validate_date(date)
        if date is not None
        else datetime.date.today().isoformat()
    )
    vals: dict = {
        "task_id": task_id,
        "unit_amount": hours,
        "date": entry_date,
    }
    project = task_project_id(url, db, uid, password, task_id)
    if project:
        vals["project_id"] = project[0]
    employee = my_employee_id(url, db, uid, password)
    if employee:
        vals["employee_id"] = employee
    if description:
        vals["name"] = description
    entry_id = execute_kw(
        url, db, uid, password, "account.analytic.line", "create", [vals]
    )
    if not isinstance(entry_id, int) or entry_id <= 0:
        raise OdooRpcError(f"Timesheet create returned {entry_id!r}.")
    return entry_id


def update_timesheet(
    url: str,
    db: str,
    uid: int,
    password: str,
    entry_id: int,
    hours: float | None = None,
    date: str | None = None,
    description: str | None = None,
    task_id: int | None = None,
) -> None:
    """Modify an existing timesheet entry."""
    vals: dict = {}
    if hours is not None:
        if hours <= 0:
            raise ValueError("Hours must be greater than 0.")
        vals["unit_amount"] = hours
    if date is not None:
        vals["date"] = _validate_date(date)
    if description is not None:
        vals["name"] = description
    if task_id is not None:
        project = task_project_id(url, db, uid, password, task_id)
        vals["task_id"] = task_id
        if project:
            vals["project_id"] = project[0]
    if not vals:
        raise ValueError("Nothing to update - pass --hours, --date, --description or --task.")
    result = execute_kw(
        url, db, uid, password, "account.analytic.line", "write",
        [[entry_id], vals],
    )
    if result is not True:
        raise OdooRpcError(
            f"Write to timesheet entry {entry_id} failed (server returned {result!r})."
        )


def fetch_timesheets(
    url: str,
    db: str,
    uid: int,
    password: str,
    task_id: int | None = None,
    days: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return the current user's timesheet entries, optionally filtered."""
    domain = [["user_id", "=", uid]]
    if task_id:
        domain.append(["task_id", "=", task_id])
    if days:
        since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        domain.append(["date", ">=", since])
    kwargs = {"fields": TIMESHEET_FIELDS, "order": "date desc, id desc"}
    if limit:
        kwargs["limit"] = limit
    return execute_kw(
        url, db, uid, password, "account.analytic.line", "search_read", [domain], kwargs
    )
