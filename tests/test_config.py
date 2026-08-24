import os
import tempfile
import unittest

from odoo_mcp.config import ConfigError, OdooConfig, load_env_file


class OdooConfigDefaultsTests(unittest.TestCase):
    def test_defaults_applied_when_url_db_absent(self) -> None:
        cfg = OdooConfig.from_env(
            {"ODOO_USERNAME": "u@example.com", "ODOO_PASSWORD": "p"}
        )

        self.assertEqual(cfg.url, "https://odoo.geosigmoid.group")
        self.assertEqual(cfg.db, "odoo")

    def test_env_overrides_win(self) -> None:
        env = {
            "ODOO_URL": "https://x.example",
            "ODOO_DB": "mydb",
            "ODOO_USERNAME": "u@example.com",
            "ODOO_PASSWORD": "p",
        }

        cfg = OdooConfig.from_env(env)

        self.assertEqual(
            (cfg.url, cfg.db, cfg.username, cfg.password),
            ("https://x.example", "mydb", "u@example.com", "p"),
        )

    def test_config_is_frozen(self) -> None:
        cfg = OdooConfig.from_env(
            {"ODOO_USERNAME": "u@example.com", "ODOO_PASSWORD": "p"}
        )

        with self.assertRaises(Exception):
            cfg.username = "other"  # type: ignore[misc]


class OdooConfigMissingCredentialsTests(unittest.TestCase):
    def test_missing_credentials_named_without_values(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            OdooConfig.from_env({})

        msg = str(ctx.exception)
        self.assertIn("ODOO_USERNAME", msg)
        self.assertIn("ODOO_PASSWORD", msg)

    def test_error_message_never_contains_credential_values(self) -> None:
        env = {"ODOO_USERNAME": "", "ODOO_PASSWORD": "dummy-pass-123"}

        with self.assertRaises(ConfigError) as ctx:
            OdooConfig.from_env(env)

        msg = str(ctx.exception)
        self.assertIn("ODOO_USERNAME", msg)
        self.assertNotIn("dummy-pass-123", msg)


class LoadEnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env_path = os.path.join(self._tmp.name, ".env")
        self._tracked: list[str] = []

    def _write_env(self, content: str) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _setenv(self, key: str, value: str) -> None:
        os.environ[key] = value
        self._tracked.append(key)

    def tearDown(self) -> None:
        for key in self._tracked:
            del os.environ[key]

    def test_missing_file_is_a_noop(self) -> None:
        load_env_file(os.path.join(self._tmp.name, "does-not-exist"))

    def test_fills_missing_or_empty_variable_from_file(self) -> None:
        self._write_env("ODOO_USERNAME=fill@example.com\n")
        self._setenv("ODOO_USERNAME", "")

        load_env_file(self.env_path)

        self.assertEqual(os.environ["ODOO_USERNAME"], "fill@example.com")

    def test_empty_string_placeholder_is_replaced(self) -> None:
        self._write_env("ODOO_PASSWORD=from-dot-env\n")
        self._setenv("ODOO_PASSWORD", "")

        load_env_file(self.env_path)

        self.assertEqual(os.environ["ODOO_PASSWORD"], "from-dot-env")

    def test_real_value_wins_over_file(self) -> None:
        self._write_env("ODOO_USERNAME=from-dot-env@example.com\n")
        self._setenv("ODOO_USERNAME", "real@example.com")

        load_env_file(self.env_path)

        self.assertEqual(os.environ["ODOO_USERNAME"], "real@example.com")

    def test_quotes_comments_and_blanks_are_handled(self) -> None:
        self._write_env(
            "# comment line\n"
            "\n"
            "ODOO_URL = 'https://quoted.example' \n"
            'ODOO_DB="quoted-db"\n'
            "no-equals-line\n"
            "=novalue\n"
        )
        self._setenv("ODOO_URL", "")
        self._setenv("ODOO_DB", "")

        load_env_file(self.env_path)

        self.assertEqual(os.environ["ODOO_URL"], "https://quoted.example")
        self.assertEqual(os.environ["ODOO_DB"], "quoted-db")


if __name__ == "__main__":
    unittest.main()
