# SQLcl MCP over Streamable HTTP

`sqlcl_http.py` starts Oracle's own SQLcl MCP server (`sql -mcp`, stdio only) once and serves all of its tools
as Streamable HTTP at `http://127.0.0.1:8770/mcp`. `/health` answers `{"status":"ok"}`.

* **One database login, shared.** SQLcl holds one connection; everyone who reaches this URL uses it.
* **Read-only by default.** Only SELECT/WITH/DESCRIBE/SHOW and read-type SQLcl commands run. Everything else
  is refused unless the caller sends `X-MCP-Allow-Destructive: true`. (SQLcl's own AUTOCOMMIT is ON, so a
  write is permanent at once.) The guard checks `sql_run` (argument `sql`) and `sqlcl_run` (argument `sqlcl`);
  `skills_sync`, `annotation_generate` and any tool it does not know also need the header. Reading tools
  (`connections_list`, `connect`, `disconnect`, `schema_information`, `context_retrieve`, `request_status`) always work.
* **Local only.** Listening on another address needs `SQLCL_MCP_TOKEN` (`Authorization: Bearer ...`).
* Not for Azure deployment as it is: one process, one login, saved connection on this machine.

## Run (Command Prompt, folder `C:\work\sqlcl-mcp`)
```cmd
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
set JAVA_HOME=C:\tools\jdk-21
set PATH=%JAVA_HOME%\bin;%PATH%
set SQLCL_COMMAND=C:\tools\sqlcl\bin\sql.exe
python sqlcl_http.py --list-tools
python sqlcl_http.py
```
Settings: `SQLCL_COMMAND`, `SQLCL_ARGS` (default `-mcp`), `SQLCL_MCP_HOST`, `SQLCL_MCP_PORT`, `SQLCL_MCP_TOKEN`,
`SQLCL_STARTUP_TIMEOUT`.

## Test
`pytest -q` (uses a stand-in for SQLcl; no Oracle, no Java). Then copy `mcp.json.example` to `.mcp.json`.
