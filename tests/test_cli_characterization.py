"""Characterization tests locking the CURRENT CLI behavior of odoo_tasks.py.

These tests pin the observable contract (exit codes 0/1/2, output strings)
before the codebase is refactored into the odoo_mcp package.

SEAM RULE: only module-level function seams of odoo_tasks are patched
(authenticate, fetch_state_selection, fetch_my_tasks, fetch_task,
update_task). `execute_kw` is NEVER patched here: that seam moves to
odoo_mcp.rpc during the refactor and patching it would silently leak real
network calls afterwards. No test in this file touches the network.
"""

import io
import json
import unittest
from unittest.mock import patch

import odoo_tasks


# Realistic selection for this Odoo build (states are NOT hardcoded in the CLI).
SELECTION = [
    ("01_in_progress", "In Progress"),
    ("04_waiting_normal", "Waiting"),
    ("07_finished", "Done"),
    ("08_void", "Cancelled"),
]

CREDENTIALS = ["-u", "user@example.com", "-p", "dummy-key"]


class CliCharacterizationTests(unittest.TestCase):
    def test_default_list_renders_table_and_exits_zero(self) -> None:
        # Given one assigned task returned by the (mocked) domain layer
        tasks = [
            {
                "id": 252,
                "name": "Write audit report",
                "state": "01_in_progress",
                "priority": 1,
                "progress": 40.0,
                "date_deadline": "2026-09-01",
                "stage_id": [3, "In Development"],
                "project_id": [7, "Audit"],
                "user_ids": [[1, "Alice"]],
            }
        ]

        # When the default list command runs through main()
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "fetch_state_selection", return_value=SELECTION),
            patch.object(odoo_tasks, "fetch_my_tasks", return_value=tasks) as fetch_my_tasks,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.main(list(CREDENTIALS))

        # Then the table rendered by format_tasks is printed and exit code is 0
        self.assertEqual(status, 0)
        self.assertIn("Write audit report", stdout.getvalue())
        self.assertIn("1 task(s)", stdout.getvalue())
        self.assertEqual(fetch_my_tasks.call_args.kwargs["include_done"], False)
        self.assertEqual(fetch_my_tasks.call_args.kwargs["limit"], 0)

    def test_show_json_prints_fetched_task_and_exits_zero(self) -> None:
        # Given a full task row as returned for SHOW_FIELDS
        task = {
            "id": 252,
            "name": "Write audit report",
            "state": "01_in_progress",
            "priority": 2,
            "progress": 40.0,
            "description": "<p>Scope the audit</p>",
            "date_deadline": "2026-09-01",
            "stage_id": [3, "In Development"],
            "project_id": [7, "Audit"],
            "user_ids": [[1, "Alice"]],
            "create_date": "2026-08-01",
            "allocated_hours": 10.0,
            "effective_hours": 4.0,
            "subtask_effective_hours": 0.0,
            "parent_id": False,
            "write_date": "2026-08-20",
        }

        # When `show 252 --json` runs through main()
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "fetch_task", return_value=task) as fetch_task,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.main(CREDENTIALS + ["show", "252", "--json"])

        # Then stdout is exactly the fetched task serialized as JSON, exit 0
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), task)
        self.assertEqual(fetch_task.call_args.args[4], 252)
        self.assertEqual(fetch_task.call_args.args[5], odoo_tasks.SHOW_FIELDS)

    def test_update_unknown_state_exits_one_with_error(self) -> None:
        # Given a state code the server does not offer
        args = odoo_tasks.parse_args(
            CREDENTIALS + ["update", "252", "--state", "99_bogus"]
        )

        # When run_update validates it against the (mocked) server selection
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(
                odoo_tasks, "fetch_state_selection", return_value=SELECTION
            ),
            patch.object(odoo_tasks, "update_task") as update_task,
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            status = odoo_tasks.run_update(args)

        # Then it fails with exit 1, names the unknown state, and writes nothing
        self.assertEqual(status, 1)
        self.assertIn("Unknown state", stderr.getvalue())
        self.assertIn("99_bogus", stderr.getvalue())
        update_task.assert_not_called()

    def test_post_whitespace_only_message_exits_two(self) -> None:
        # Given a message that contains no non-whitespace characters
        args = odoo_tasks.parse_args(["post", "252", "--message", "   \t  "])

        # When run_post validates the message before any RPC happens
        with (
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            status = odoo_tasks.run_post(args)

        # Then it exits 2 with the exact usage error on stderr
        self.assertEqual(status, 2)
        self.assertIn("Nothing to post - pass a non-empty --message.", stderr.getvalue())

    def test_timesheet_list_defaults_to_limit_50(self) -> None:
        # When the timesheet list subcommand is parsed (pure parsing, no mocks)
        args = odoo_tasks.parse_args(["timesheet", "list"])

        # Then the default limit of 50 entries applies
        self.assertEqual(args.command, "timesheet")
        self.assertEqual(args.ts_action, "list")
        self.assertEqual(args.limit, 50)


if __name__ == "__main__":
    unittest.main()
