import unittest

from odoo_mcp.tasks import validate_update_vals


class ValidateUpdateValsTests(unittest.TestCase):
    def test_builds_vals_from_provided_fields(self) -> None:
        vals = validate_update_vals(
            name="  Renamed  ",
            description="Audit done",
            progress=50.0,
            allocated_hours=40.0,
            priority=2,
            deadline="2026-08-21",
            state="01_in_progress",
        )

        self.assertEqual(
            vals,
            {
                "name": "Renamed",
                "description": "Audit done",
                "progress": 50.0,
                "allocated_hours": 40.0,
                "priority": 2,
                "date_deadline": "2026-08-21",
                "state": "01_in_progress",
            },
        )

    def test_progress_out_of_bounds_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_update_vals(progress=101)
        with self.assertRaises(ValueError):
            validate_update_vals(progress=-0.5)

    def test_non_finite_progress_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_update_vals(progress=float("nan"))

        with self.assertRaises(ValueError):
            validate_update_vals(progress=float("inf"))

    def test_negative_allocated_hours_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_update_vals(allocated_hours=-1)

    def test_impossible_deadline_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_update_vals(deadline="2026-02-30")

    def test_no_fields_yields_empty_vals(self) -> None:
        self.assertEqual(validate_update_vals(), {})


if __name__ == "__main__":
    unittest.main()
