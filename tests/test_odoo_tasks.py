import io
import unittest
from unittest.mock import patch

import odoo_mcp.tasks
import odoo_tasks


class DateValidationTests(unittest.TestCase):
    def test_validate_date_rejects_impossible_calendar_date(self) -> None:
        # Given an ISO-shaped value that is not a calendar date
        value = "2026-02-30"

        # When the CLI parses it at its input boundary
        # Then the value is rejected before an RPC request is made
        with self.assertRaises(ValueError):
            odoo_tasks._validate_date(value)


class TaskFilteringTests(unittest.TestCase):
    def test_terminal_state_codes_derive_from_server_values(self) -> None:
        selection = [
            ("01_in_progress", "In Progress"),
            ("04_waiting_normal", "Waiting"),
            ("07_finished", "Done"),
            ("08_void", "Cancelled"),
        ]

        terminal = odoo_tasks.terminal_state_codes(selection)

        self.assertEqual(terminal, ["07_finished", "08_void"])

    def test_fetch_my_tasks_applies_all_requested_filters(self) -> None:
        filters = odoo_tasks.TaskFilters(
            project_id=12,
            stage_id=34,
            state="01_in_progress",
            due_after="2026-08-01",
            due_before="2026-08-31",
        )
        with patch.object(odoo_mcp.tasks, "execute_kw", return_value=[]) as execute:
            result = odoo_tasks.fetch_my_tasks(
                "https://odoo.example",
                "odoo",
                1,
                "key",
                include_done=False,
                limit=10,
                terminal_states=["07_finished"],
                filters=filters,
            )

        self.assertEqual(result, [])
        domain = execute.call_args.args[6][0]
        self.assertEqual(
            domain,
            [
                ["user_ids", "in", 1],
                ["state", "not in", ["07_finished"]],
                ["project_id", "=", 12],
                ["stage_id", "=", 34],
                ["state", "=", "01_in_progress"],
                ["date_deadline", ">=", "2026-08-01"],
                ["date_deadline", "<=", "2026-08-31"],
            ],
        )


class DryRunTests(unittest.TestCase):
    def test_task_update_dry_run_does_not_update_task(self) -> None:
        args = odoo_tasks.parse_args(["update", "252", "--name", "Renamed", "--dry-run"])
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "update_task") as update_task,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.run_update(args)

        self.assertEqual(status, 0)
        self.assertIn('"name": "Renamed"', stdout.getvalue())
        update_task.assert_not_called()

    def test_post_dry_run_does_not_post_message(self) -> None:
        args = odoo_tasks.parse_args(["post", "252", "--message", "Progress", "--dry-run"])
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "post_message") as post_message,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.run_post(args)

        self.assertEqual(status, 0)
        self.assertIn("would post to task 252", stdout.getvalue())
        post_message.assert_not_called()

    def test_timesheet_create_dry_run_does_not_create_entry(self) -> None:
        args = odoo_tasks.parse_args(
            ["timesheet", "create", "252", "--hours", "2", "--dry-run"]
        )
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "create_timesheet") as create_timesheet,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.run_timesheet(args)

        self.assertEqual(status, 0)
        self.assertIn("would log 2h on task 252", stdout.getvalue())
        create_timesheet.assert_not_called()

    def test_timesheet_update_dry_run_does_not_update_entry(self) -> None:
        args = odoo_tasks.parse_args(
            ["timesheet", "update", "17", "--hours", "3.5", "--dry-run"]
        )
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1),
            patch.object(odoo_tasks, "update_timesheet") as update_timesheet,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            status = odoo_tasks.run_timesheet(args)

        self.assertEqual(status, 0)
        self.assertIn("would update timesheet entry 17", stdout.getvalue())
        update_timesheet.assert_not_called()


class CredentialTests(unittest.TestCase):
    def test_main_reads_password_from_standard_input(self) -> None:
        with (
            patch.object(odoo_tasks, "authenticate", return_value=1) as authenticate,
            patch.object(odoo_tasks, "fetch_state_selection", return_value=[]),
            patch.object(odoo_tasks, "fetch_my_tasks", return_value=[]),
            patch("sys.stdin", io.StringIO("secret-key\n")),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            status = odoo_tasks.main(["--username", "user@example.com", "--password-stdin"])

        self.assertEqual(status, 0)
        self.assertEqual(authenticate.call_args.args[3], "secret-key")


if __name__ == "__main__":
    unittest.main()
