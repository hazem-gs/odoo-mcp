#!/usr/bin/env python3
"""Pull tasks assigned to the current user from a self-hosted Odoo instance.

One-shot JSON-RPC client (stdlib only, no third-party dependencies).

Usage:
    python odoo_tasks.py                     # uses env vars for credentials
    printf '%s' "$ODOO_PASSWORD" | python odoo_tasks.py -u you@co.com --password-stdin
    python odoo_tasks.py --json              # machine-readable output
    python odoo_tasks.py --all               # include done/cancelled tasks
    python odoo_tasks.py --limit 50
    python odoo_tasks.py show 252            # full details of one task
    python odoo_tasks.py post 252 --message "Progress report"   # post to chatter
    python odoo_tasks.py update 252 --allocated-hours 40        # planned effort
    python odoo_tasks.py update 252 --append-description "Audit done" --stage "In Development"
    python odoo_tasks.py update 252 --state 1_done              # state validated against the server
    python odoo_tasks.py timesheet create 252 --hours 2 --description "Compile plugins"
    python odoo_tasks.py timesheet update 17 --hours 3.5 --date 2026-08-14
    python odoo_tasks.py timesheet list [--task 252] [--days 7]

Credentials are resolved in this order (first wins):
  1. CLI arguments (-u / -p)
  2. Real environment variables
  3. A .env file in this script's directory (if present)
  4. Built-in defaults

Environment variables:
    ODOO_URL       (default: https://odoo.geosigmoid.group)
    ODOO_DB        (default: odoo)
    ODOO_USERNAME  (your login email)
    ODOO_PASSWORD  your password OR an external API key
                   (Preferences -> Account Security -> New API Key)

The API key is recommended: it is revocable and cannot be used for web login.
"""

import argparse
import datetime
import json
import os
import sys

from odoo_mcp.config import ODOO_DEFAULT_DB as DEFAULT_DB, ODOO_DEFAULT_URL as DEFAULT_URL
from odoo_mcp.rpc import OdooRpcError, authenticate, execute_kw, json_rpc
from odoo_mcp.timesheets import (
    TIMESHEET_FIELDS,
    create_timesheet,
    fetch_timesheets,
    my_employee_id,
    task_project_id,
    update_timesheet,
)
from odoo_mcp.tasks import (
    PRIORITY_LABELS,
    SHOW_FIELDS,
    TASK_FIELDS,
    TaskFilters,
    _validate_date,
    fetch_my_tasks,
    fetch_state_selection,
    fetch_task,
    find_stage_id,
    post_message,
    terminal_state_codes,
    update_task,
    validate_update_vals,
)

def load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from *path* into os.environ (no override).

    Lines starting with '#' are ignored, as are empty lines. Values may be
    wrapped in single or double quotes. Existing environment variables win.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def format_task_detail(task: dict) -> str:
    """Render a single task as key/value lines for the 'show' command."""
    progress = task.get("progress")
    progress_txt = f"{progress:g}%" if progress else "-"
    lines = [
        f"Task {task.get('id')}: {(task.get('name') or '').strip()}",
        f"  Project    : {rel_name(task.get('project_id'))}",
        f"  Stage      : {rel_name(task.get('stage_id'))}",
        f"  State      : {task.get('state') or '-'}",
        f"  Priority   : {PRIORITY_LABELS.get(task.get('priority'), task.get('priority'))}",
        f"  Progress   : {progress_txt}",
        f"  Deadline   : {task.get('date_deadline') or '-'}",
        f"  Created    : {task.get('create_date') or '-'}",
        f"  Planned h  : {task.get('allocated_hours') or 0:g}",
        f"  Logged h   : {task.get('effective_hours') or 0:g} (subtasks {task.get('subtask_effective_hours') or 0:g})",
    ]
    if task.get("parent_id"):
        lines.append(f"  Parent     : {rel_name(task.get('parent_id'))}")
    desc = (task.get("description") or "").strip()
    lines.append(f"  Description: ({len(desc)} chars)")
    if desc:
        lines.append("  " + desc.replace("\n", "\n  "))
    return "\n".join(lines)


def format_timesheets(entries: list[dict]) -> str:
    """Render timesheet entries as a human-readable table."""
    if not entries:
        return "No timesheet entries found."

    rows = []
    for e in entries:
        rows.append(
            {
                "id": str(e.get("id")),
                "date": e.get("date") or "-",
                "task": rel_name(e.get("task_id")),
                "project": rel_name(e.get("project_id")),
                "hours": f"{e.get('unit_amount') or 0:g}",
                "desc": (e.get("name") or "").strip() or "-",
            }
        )

    headers = {
        "id": "ID",
        "date": "Date",
        "task": "Task",
        "project": "Project",
        "hours": "Hours",
        "desc": "Description",
    }
    widths = {}
    for key, label in headers.items():
        width = max(len(label), max(len(r[key]) for r in rows))
        if key in ("task", "desc"):
            width = min(width, 50)
        widths[key] = width

    header = "  ".join(f"{label:<{widths[k]}}" for k, label in headers.items())
    lines = [header, "-" * len(header)]
    for r in rows:
        cells = []
        for k in headers:
            value = r[k]
            if k in ("task", "desc") and len(value) > widths[k]:
                value = value[: widths[k] - 3] + "..."
            cells.append(f"{value:<{widths[k]}}")
        lines.append("  ".join(cells))
    lines.append(f"\n{len(rows)} entry(ies)")
    return "\n".join(lines)


def rel_name(value) -> str:
    """Render a many2one field value: [id, display_name] or False."""
    if isinstance(value, list) and len(value) == 2:
        return str(value[1])
    if value:
        return str(value)
    return "-"


def format_tasks(tasks: list[dict]) -> str:
    """Render tasks as a human-readable table."""
    if not tasks:
        return "No tasks found."

    rows = []
    for t in tasks:
        priority = PRIORITY_LABELS.get(t.get("priority"), str(t.get("priority", "-")))
        assignees = t.get("user_ids") or []
        if assignees and isinstance(assignees[0], (list, tuple)):
            assignee_names = ", ".join(str(u[1]) for u in assignees)
        else:
            assignee_names = ", ".join(str(u) for u in assignees) or "-"
        rows.append(
            {
                "id": t.get("id"),
                "task": (t.get("name") or "").strip() or "(untitled)",
                "project": rel_name(t.get("project_id")),
                "stage": rel_name(t.get("stage_id")),
                "state": t.get("state") or "-",
                "priority": priority,
                "progress": f"{t.get('progress'):g}%" if t.get("progress") else "-",
                "deadline": t.get("date_deadline") or "-",
                "assignees": assignee_names,
            }
        )

    widths = {
        "id": max(len(str(r["id"])) for r in rows),
        "task": min(max(len(r["task"]) for r in rows), 60),
        "project": min(max(len(r["project"]) for r in rows), 25),
        "stage": min(max(len(r["stage"]) for r in rows), 20),
        "state": max(len(r["state"]) for r in rows),
        "priority": max(len(r["priority"]) for r in rows),
        "progress": max(max(len(r["progress"]) for r in rows), len("Prog")),
        "deadline": max(len(r["deadline"]) for r in rows),
    }

    header = (
        f"{'ID':<{widths['id']}}  "
        f"{'Task':<{widths['task']}}  "
        f"{'Project':<{widths['project']}}  "
        f"{'Stage':<{widths['stage']}}  "
        f"{'State':<{widths['state']}}  "
        f"{'Prio':<{widths['priority']}}  "
        f"{'Prog':<{widths['progress']}}  "
        f"{'Deadline':<{widths['deadline']}}"
    )
    sep = "-" * len(header)

    lines = [header, sep]
    for r in rows:
        task = r["task"]
        if len(task) > widths["task"]:
            task = task[: widths["task"] - 3] + "..."
        lines.append(
            f"{r['id']:<{widths['id']}}  "
            f"{task:<{widths['task']}}  "
            f"{r['project']:<{widths['project']}}  "
            f"{r['stage']:<{widths['stage']}}  "
            f"{r['state']:<{widths['state']}}  "
            f"{r['priority']:<{widths['priority']}}  "
            f"{r['progress']:<{widths['progress']}}  "
            f"{r['deadline']:<{widths['deadline']}}"
        )
    lines.append(f"\n{len(rows)} task(s)")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pull tasks assigned to you from a self-hosted Odoo instance.",
        epilog="Credentials: CLI args take precedence over ODOO_URL/ODOO_DB/"
        "ODOO_USERNAME/ODOO_PASSWORD environment variables.",
    )
    p.add_argument("--url", default=os.environ.get("ODOO_URL", DEFAULT_URL))
    p.add_argument("--db", default=os.environ.get("ODOO_DB", DEFAULT_DB))
    p.add_argument("-u", "--username", default=os.environ.get("ODOO_USERNAME"))
    credential = p.add_mutually_exclusive_group()
    credential.add_argument(
        "-p",
        "--password",
        default=os.environ.get("ODOO_PASSWORD"),
        help="Password or external API key (visible to process listings; prefer --password-stdin)",
    )
    credential.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password or external API key from standard input",
    )
    p.add_argument("--json", action="store_true", help="Output raw JSON instead of a table")
    p.add_argument("--all", action="store_true", help="Include done and cancelled tasks")
    p.add_argument("--limit", type=int, default=0, help="Maximum number of tasks (default: no limit)")
    p.add_argument("--project-id", type=int, help="Only tasks in this project ID")
    p.add_argument("--stage-id", type=int, help="Only tasks in this stage ID")
    p.add_argument("--state", help="Only tasks in this workflow state")
    p.add_argument("--due-after", metavar="YYYY-MM-DD", help="Only tasks due on or after this date")
    p.add_argument("--due-before", metavar="YYYY-MM-DD", help="Only tasks due on or before this date")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    upd = sub.add_parser("update", help="Update fields of a task")
    upd.add_argument("task_id", type=int, help="Task ID to update")
    upd.add_argument("--name", help="Set the task title")
    upd.add_argument("--description", help="Overwrite the task description")
    upd.add_argument(
        "--append-description",
        help="Append text to the task description (e.g. a progress report)",
    )
    upd.add_argument(
        "--progress", type=float, metavar="0-100",
        help="Set progress percentage (computed field - may be overwritten)",
    )
    upd.add_argument(
        "--allocated-hours", type=float, metavar="H",
        help="Set planned hours (drives the computed progress field)",
    )
    upd.add_argument("--priority", type=int, choices=[0, 1, 2, 3], help="Set priority (0 low - 3 urgent)")
    upd.add_argument("--deadline", metavar="YYYY-MM-DD", help="Set the deadline")
    upd.add_argument("--state", metavar="STATE", help="Set workflow state (validated against the server)")
    upd.add_argument("--stage", help="Set stage by exact name, e.g. 'In Development'")
    upd.add_argument("--dry-run", action="store_true", help="Preview the update without changing Odoo")

    show = sub.add_parser("show", help="Show full details of a task")
    show.add_argument("task_id", type=int, help="Task ID to inspect")
    show.add_argument("--json", action="store_true", help="Output raw JSON")

    post = sub.add_parser("post", help="Post a message to a task's chatter")
    post.add_argument("task_id", type=int, help="Task ID to post to")
    post.add_argument("--message", required=True, help="Message body (plain text or HTML)")
    post.add_argument("--dry-run", action="store_true", help="Preview the post without changing Odoo")

    ts = sub.add_parser("timesheet", help="Create, update or list timesheet entries")
    ts_sub = ts.add_subparsers(dest="ts_action", metavar="ACTION", required=True)

    ts_create = ts_sub.add_parser("create", help="Log hours on a task")
    ts_create.add_argument("task_id", type=int, help="Task ID to log time against")
    ts_create.add_argument("--hours", type=float, required=True, help="Hours to log")
    ts_create.add_argument(
        "--date", default=datetime.date.today().isoformat(),
        help="Entry date (YYYY-MM-DD, default: today)",
    )
    ts_create.add_argument("--description", help="What was done")
    ts_create.add_argument("--dry-run", action="store_true", help="Preview the entry without changing Odoo")

    ts_update = ts_sub.add_parser("update", help="Modify an existing timesheet entry")
    ts_update.add_argument("entry_id", type=int, help="Timesheet entry ID")
    ts_update.add_argument("--hours", type=float, help="New hours")
    ts_update.add_argument("--date", help="New date (YYYY-MM-DD)")
    ts_update.add_argument("--description", help="New description")
    ts_update.add_argument("--task", type=int, help="Move entry to this task ID")
    ts_update.add_argument("--dry-run", action="store_true", help="Preview the update without changing Odoo")

    ts_list = ts_sub.add_parser("list", help="Show your timesheet entries")
    ts_list.add_argument("--task", type=int, help="Only entries for this task ID")
    ts_list.add_argument("--days", type=int, help="Only entries from the last N days")
    ts_list.add_argument("--limit", type=int, default=50, help="Maximum entries (default: 50)")

    return p.parse_args(argv)


def run_update(args: argparse.Namespace) -> int:
    """Handle the 'update <TASK_ID> --field value' command."""
    update_fields = (
        args.name,
        args.description,
        args.append_description,
        args.progress,
        args.allocated_hours,
        args.priority,
        args.deadline,
        args.state,
        args.stage,
    )
    if all(f is None for f in update_fields):
        print(
            "Nothing to update - pass at least one field, e.g. --allocated-hours 40.",
            file=sys.stderr,
        )
        return 2
    try:
        uid = authenticate(args.url, args.db, args.username, args.password)
        if args.state:
            valid = [v for v, _ in fetch_state_selection(args.url, args.db, uid, args.password)]
            if args.state not in valid:
                raise ValueError(
                    f"Unknown state {args.state!r}. Valid states on this server: "
                    + ", ".join(valid)
                )
        vals = validate_update_vals(
            name=args.name,
            description=args.description,
            progress=args.progress,
            allocated_hours=args.allocated_hours,
            priority=args.priority,
            deadline=args.deadline,
            state=args.state,
        )
        if args.stage:
            vals["stage_id"] = find_stage_id(args.url, args.db, uid, args.password, args.stage)
        if args.append_description:
            current = fetch_task(
                args.url, args.db, uid, args.password, args.task_id, ["description"]
            )
            base = (current or {}).get("description") or ""
            vals["description"] = base.rstrip() + "\n\n" + args.append_description
        if args.dry_run:
            print(f"Dry run: would update task {args.task_id} with:")
            print(json.dumps(vals, indent=2, ensure_ascii=False, default=str))
            return 0
        update_task(args.url, args.db, uid, args.password, args.task_id, vals)
        tasks = execute_kw(
            args.url,
            args.db,
            uid,
            args.password,
            "project.task",
            "search_read",
            [[["id", "=", args.task_id]]],
            {"fields": TASK_FIELDS},
        )
        if not tasks:
            raise OdooRpcError(f"Task {args.task_id} not found or no read access.")
    except (OdooRpcError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.progress is not None:
        print(
            "Warning: 'progress' is a computed field (logged hours / planned hours); "
            "the value may be recomputed. Use --allocated-hours to set planned effort.",
            file=sys.stderr,
        )
    print(f"Updated task {args.task_id}:")
    print(format_tasks(tasks))
    return 0


def run_timesheet(args: argparse.Namespace) -> int:
    """Handle 'timesheet create|update|list'."""
    try:
        uid = authenticate(args.url, args.db, args.username, args.password)
        if args.ts_action == "create":
            if args.dry_run:
                if args.hours <= 0:
                    raise ValueError("Hours must be greater than 0.")
                entry_date = _validate_date(args.date)
                print(
                    f"Dry run: would log {args.hours:g}h on task {args.task_id} "
                    f"for {entry_date}."
                )
                return 0
            entry_id = create_timesheet(
                args.url, args.db, uid, args.password,
                args.task_id, args.hours,
                date=args.date, description=args.description,
            )
            print(f"Logged {args.hours:g}h on task {args.task_id} (entry {entry_id}).")
            return 0
        if args.ts_action == "update":
            if args.dry_run:
                if args.hours is not None and args.hours <= 0:
                    raise ValueError("Hours must be greater than 0.")
                if args.date is not None:
                    _validate_date(args.date)
                if (
                    args.hours is None
                    and args.date is None
                    and args.description is None
                    and args.task is None
                ):
                    raise ValueError(
                        "Nothing to update - pass --hours, --date, --description or --task."
                    )
                print(f"Dry run: would update timesheet entry {args.entry_id}.")
                return 0
            update_timesheet(
                args.url, args.db, uid, args.password, args.entry_id,
                hours=args.hours, date=args.date,
                description=args.description, task_id=args.task,
            )
            print(f"Updated timesheet entry {args.entry_id}.")
            return 0
        entries = fetch_timesheets(
            args.url, args.db, uid, args.password,
            task_id=args.task, days=args.days, limit=args.limit,
        )
    except (OdooRpcError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(format_timesheets(entries))
    return 0


def run_show(args: argparse.Namespace) -> int:
    """Handle the 'show <TASK_ID>' command."""
    try:
        uid = authenticate(args.url, args.db, args.username, args.password)
        task = fetch_task(args.url, args.db, uid, args.password, args.task_id, SHOW_FIELDS)
    except (OdooRpcError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not task:
        print(f"Task {args.task_id} not found or no read access.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(task, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_task_detail(task))
    return 0


def run_post(args: argparse.Namespace) -> int:
    """Handle the 'post <TASK_ID> --message ...' command."""
    if not args.message.strip():
        print("Nothing to post - pass a non-empty --message.", file=sys.stderr)
        return 2
    try:
        uid = authenticate(args.url, args.db, args.username, args.password)
        if args.dry_run:
            print(f"Dry run: would post to task {args.task_id}:\n{args.message}")
            return 0
        message_id = post_message(
            args.url, args.db, uid, args.password, args.task_id, args.message
        )
    except (OdooRpcError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Posted message {message_id} to task {args.task_id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    # Odoo returns UTF-8; on Windows the console may otherwise mangle accents.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.password_stdin:
        args.password = sys.stdin.readline().rstrip("\r\n")

    if not args.username or not args.password:
        print(
            "Missing credentials. Set ODOO_USERNAME/ODOO_PASSWORD or pass -u/-p.",
            file=sys.stderr,
        )
        return 2

    if args.command == "update":
        return run_update(args)
    if args.command == "show":
        return run_show(args)
    if args.command == "post":
        return run_post(args)
    if args.command == "timesheet":
        return run_timesheet(args)

    try:
        uid = authenticate(args.url, args.db, args.username, args.password)
        selection = fetch_state_selection(args.url, args.db, uid, args.password)
        filters = TaskFilters(
            project_id=args.project_id,
            stage_id=args.stage_id,
            state=args.state,
            due_after=_validate_date(args.due_after) if args.due_after else None,
            due_before=_validate_date(args.due_before) if args.due_before else None,
        )
        tasks = fetch_my_tasks(
            args.url,
            args.db,
            uid,
            args.password,
            include_done=args.all,
            limit=args.limit,
            terminal_states=terminal_state_codes(selection),
            filters=filters,
        )
    except (OdooRpcError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
    else:
        print(format_tasks(tasks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
