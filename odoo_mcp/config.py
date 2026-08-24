"""Typed Odoo connection settings resolved from environment variables.

The Odoo URL / database default to this deployment's values; credentials are
required and must come from ODOO_USERNAME / ODOO_PASSWORD. Error messages name
missing variables but never include their values.
"""

import os
from dataclasses import dataclass

ODOO_DEFAULT_URL = "https://odoo.geosigmoid.group"
ODOO_DEFAULT_DB = "odoo"

_REQUIRED_CREDENTIAL_VARS = ("ODOO_USERNAME", "ODOO_PASSWORD")


def load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from *path* into os.environ (fill-missing mode).

    Lines starting with '#' are ignored, as are empty lines. Values may be
    wrapped in single or double quotes. A variable is only filled when it is
    absent OR set to an empty string; a real value already in the environment
    always wins. Empty-string filling matters because MCP launchers inject the
    configured ``environment`` block verbatim - unset placeholders arrive as
    empty strings rather than being omitted. Mirrors odoo_tasks.py's loader
    except for that empty-string case.
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
            if key and not os.environ.get(key):
                os.environ[key] = value


class ConfigError(RuntimeError):
    """Raised when required Odoo settings are missing from the environment."""


@dataclass(frozen=True)
class OdooConfig:
    """Connection settings for one Odoo instance."""

    url: str
    db: str
    username: str
    password: str

    @classmethod
    def from_env(cls, env=os.environ) -> "OdooConfig":
        """Build settings from *env*, applying defaults for url/db.

        Empty-string variables count as missing so a blank credential never
        silently reaches the RPC layer.
        """
        missing = [name for name in _REQUIRED_CREDENTIAL_VARS if not env.get(name)]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            url=env.get("ODOO_URL") or ODOO_DEFAULT_URL,
            db=env.get("ODOO_DB") or ODOO_DEFAULT_DB,
            username=env["ODOO_USERNAME"],
            password=env["ODOO_PASSWORD"],
        )
