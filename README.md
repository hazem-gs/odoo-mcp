# Odoo MCP server for OpenCode

This workspace ships a local **stdio MCP server** (`odoo_mcp`) that exposes your
self-hosted Odoo project tasks, chatter, and timesheets as nine typed tools an
agent can call directly. The same repository still contains the original
`odoo_tasks.py` CLI — unchanged behaviorally — which shares the service layer
with the MCP server.

## Prerequisites

- Python 3.10+ (workspace developed against 3.12)
- [uv](https://docs.astral.sh/uv/) on PATH (`pip install uv` if missing)

Install everything once:

```powershell
uv sync
```

## Credentials (environment variables)

| Variable | Purpose | Default |
| --- | --- | --- |
| `ODOO_URL` | Base URL of your Odoo instance | `https://odoo.geosigmoid.group` |
| `ODOO_DB` | Database name | `odoo` |
| `ODOO_USERNAME` | Your login email | required |
| `ODOO_PASSWORD` | Password **or external API key** | required |

An **API key is strongly recommended** over a password (Odoo: Preferences →
Account Security → New API Key): it is revocable and cannot be used for web
login. Copy `.env.example` to `.env`, fill it in, and load it into your shell,
or set the variables however you manage secrets. The MCP server and CLI helper
both auto-load a workspace-root `.env` when it is present; environment
variables already set by OpenCode or the shell take precedence. Never commit
`.env`.

**Interpolation caveat:** `opencode.json` uses `{env:VAR}` placeholders. If a
variable is missing when OpenCode starts, it becomes an **empty string**, and
every tool call will fail with a clear error naming the missing variable(s)
(`Missing required environment variables: ODOO_USERNAME, ...`) instead of
sending bad credentials anywhere. Values are never echoed to logs or results.

## Registering with OpenCode

`opencode.json` in this workspace already contains:

```jsonc
{
  "mcp": {
    "odoo": {
      "type": "local",
      "command": ["uv", "run", "python", "-m", "odoo_mcp"],
      "environment": { "...": "{env:ODOO_*} placeholders" },
      "enabled": true
    }
  }
}
```

OpenCode loads config at startup — after changing credentials or this file,
**quit and restart OpenCode** for them to take effect. The server is spawned
once per session over stdio; nothing listens on any network port.

## Tools

Mutating tools **execute immediately by default**; every one accepts a
`dry_run=true` flag that returns a preview without writing anything.

| Tool | What it does |
| --- | --- |
| `list_my_tasks` | Tasks assigned to you; filter by project, stage, state, due dates |
| `get_task` | Full detail of one task (planned/logged hours included) |
| `update_task` | Set name/description/priority/deadline/hours/state/stage; `append_description` merges text into the existing description |
| `post_task_message` | Post to a task's chatter (followers get notified) |
| `get_task_states` | Valid workflow state codes on this server + which are terminal |
| `list_stages` | Task stage ids/names for filtering |
| `list_timesheets` | Your timesheet entries; filter by task or recent days |
| `create_timesheet` | Log hours on a task (date defaults to today) |
| `update_timesheet` | Change hours/date/description/task of an entry |

State codes vary per Odoo build — call `get_task_states` first rather than
guessing; invalid states are rejected with the list of valid ones.

## Verifying the install

```powershell
uv sync                                              # deps resolve
uv run pytest tests -q                               # protocol + service suites
uv run python -m unittest discover -s tests -v       # CLI characterization suite
uv run python odoo_tasks.py --help                   # legacy CLI still intact
```

The stdio handshake itself is covered by `tests/test_stdio_smoke.py`.
