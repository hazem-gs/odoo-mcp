import datetime
import unittest
from unittest.mock import patch

import odoo_mcp.timesheets as ts


def _fake_rpc(project=(7, "Audit"), employee=(5, "Emp"), created_id=99):
    """Route fake execute_kw calls by model/method like the real service does."""

    def fake_execute_kw(url, db, uid, password, model, method, args, kwargs=None):
        if model == "project.task":
            return [{"project_id": list(project)}]
        if model == "res.users":
            return [{"employee_id": list(employee)}]
        if model == "account.analytic.line" and method == "create":
            return created_id
        if model == "account.analytic.line" and method == "write":
            return True
        if model == "account.analytic.line" and method == "search_read":
            return []
        raise AssertionError(f"unexpected call {model}.{method}")

    return fake_execute_kw


class CreateTimesheetTests(unittest.TestCase):
    def test_resolves_project_and_employee_and_creates_entry(self) -> None:
        with patch.object(
            ts, "execute_kw", side_effect=_fake_rpc()
        ) as execute:
            entry_id = ts.create_timesheet(
                "https://odoo.example", "db", 1, "pw", 252, 2.0,
                date="2026-08-21", description="Compile plugins",
            )

        self.assertEqual(entry_id, 99)
        create_calls = [
            c for c in execute.call_args_list
            if c.args[4] == "account.analytic.line" and c.args[5] == "create"
        ]
        self.assertEqual(len(create_calls), 1)
        vals = create_calls[0].args[6][0]
        self.assertEqual(
            vals,
            {
                "task_id": 252,
                "unit_amount": 2.0,
                "date": "2026-08-21",
                "project_id": 7,
                "employee_id": 5,
                "name": "Compile plugins",
            },
        )

    def test_rejects_non_positive_hours_without_rpc(self) -> None:
        with patch.object(ts, "execute_kw") as execute:
            with self.assertRaises(ValueError):
                ts.create_timesheet("https://odoo.example", "db", 1, "pw", 252, 0)

        execute.assert_not_called()

    def test_rejects_impossible_date_without_rpc(self) -> None:
        with patch.object(ts, "execute_kw") as execute:
            with self.assertRaises(ValueError):
                ts.create_timesheet(
                    "https://odoo.example", "db", 1, "pw", 252, 1.0,
                    date="2026-02-30",
                )

        execute.assert_not_called()


class UpdateTimesheetTests(unittest.TestCase):
    def test_builds_only_provided_vals(self) -> None:
        with patch.object(ts, "execute_kw", side_effect=_fake_rpc()) as execute:
            ts.update_timesheet("https://odoo.example", "db", 1, "pw", 17, hours=3.5)

        write_calls = [
            c for c in execute.call_args_list if c.args[5] == "write"
        ]
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(write_calls[0].args[6], [[17], {"unit_amount": 3.5}])

    def test_empty_update_rejected(self) -> None:
        with patch.object(ts, "execute_kw") as execute:
            with self.assertRaises(ValueError):
                ts.update_timesheet("https://odoo.example", "db", 1, "pw", 17)

        execute.assert_not_called()

    def test_task_move_resolves_project(self) -> None:
        with patch.object(ts, "execute_kw", side_effect=_fake_rpc()) as execute:
            ts.update_timesheet(
                "https://odoo.example", "db", 1, "pw", 17, task_id=300
            )

        write_calls = [c for c in execute.call_args_list if c.args[5] == "write"]
        vals = write_calls[0].args[6][1]
        self.assertEqual(vals, {"task_id": 300, "project_id": 7})


class FetchTimesheetsTests(unittest.TestCase):
    def test_composes_domain_and_forwards_default_limit_50(self) -> None:
        with patch.object(
            ts, "execute_kw", side_effect=_fake_rpc()
        ) as execute:
            entries = ts.fetch_timesheets(
                "https://odoo.example", "db", 1, "pw", task_id=252, days=7
            )

        self.assertEqual(entries, [])
        call = execute.call_args_list[-1]
        expected_since = (
            datetime.date.today() - datetime.timedelta(days=7)
        ).isoformat()
        self.assertEqual(
            call.args[6][0],
            [
                ["user_id", "=", 1],
                ["task_id", "=", 252],
                ["date", ">=", expected_since],
            ],
        )
        self.assertEqual(call.args[7]["limit"], 50)


if __name__ == "__main__":
    unittest.main()
