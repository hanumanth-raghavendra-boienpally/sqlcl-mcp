"""Streamable-HTTP front door for Oracle's own SQLcl MCP server (`sql -mcp`, stdio only).

It starts SQLcl once, keeps that one process (and its one database login) alive, and serves every
tool SQLcl offers at  http://HOST:PORT/mcp  (Streamable HTTP).  /health answers {"status":"ok"}.

What it adds on top of SQLcl:
  * Read-only by default. Anything that is not a plain SELECT/WITH/DESCRIBE/SHOW (and not a
    harmless SQLcl command) is refused unless the caller sends  X-MCP-Allow-Destructive: true.
  * Local by default. It listens on 127.0.0.1. To listen on another address you must set
    SQLCL_MCP_TOKEN; callers then send  Authorization: Bearer <token>  or  X-MCP-Token: <token>.
  * One database login shared by everyone who reaches it (that is how SQLcl works).

Settings (environment variables; all optional):
  SQLCL_COMMAND  path of the SQLcl launcher (default: sql)       SQLCL_ARGS  default: -mcp
  SQLCL_MCP_HOST default 127.0.0.1    SQLCL_MCP_PORT default 8770    SQLCL_MCP_TOKEN  default: none
  SQLCL_STARTUP_TIMEOUT seconds to wait for SQLcl to start (default 60)

Run:  python sqlcl_http.py            List what SQLcl offers and exit:  python sqlcl_http.py --list-tools
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import re
import shlex
import sys

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyProvider
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.responses import JSONResponse

__version__ = "0.1.0"
log = logging.getLogger("sqlcl_http")

ALLOW_HEADER = "x-mcp-allow-destructive"
TOKEN_HEADERS = ("x-mcp-token",)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# First words of SQL that only read. Anything else needs the allow-destructive header.
READ_SQL = {"select", "with", "explain", "describe", "desc", "show"}
# First words of SQLcl commands that only read or only change the screen/session look.
READ_SQLCL = {"desc", "describe", "show", "help", "info", "ddl", "set", "column", "col",
              "select", "with", "explain", "history", "clear", "pwd"}
# Tools that only read, or only open/close the session. Any tool not listed here or in SQL_TOOLS needs the header.
SAFE_TOOLS = {"connections_list", "list_connections", "connect", "disconnect", "schema_information",
              "context_retrieve", "request_status"}
# Tools that run text: the argument that holds it, and the first words that count as reading.
SQL_TOOLS = {"sql_run": ("sql", READ_SQL), "run_sql": ("sql", READ_SQL),
             "sqlcl_run": ("sqlcl", READ_SQLCL), "run_sqlcl": ("sqlcl", READ_SQLCL)}


def _settings() -> dict:
    return {
        "command": os.environ.get("SQLCL_COMMAND", "sql"),
        "args": shlex.split(os.environ.get("SQLCL_ARGS", "-mcp"), posix=(os.name != "nt")),
        "host": os.environ.get("SQLCL_MCP_HOST", "127.0.0.1"),
        "port": int(os.environ.get("SQLCL_MCP_PORT", "8770")),
        "token": os.environ.get("SQLCL_MCP_TOKEN", ""),
        "startup_timeout": float(os.environ.get("SQLCL_STARTUP_TIMEOUT", "60")),
    }


# ---------------------------------------------------------------- the read-only guard
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRINGS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _statements(text: str) -> list[str]:
    """Split on ; and / lines after removing comments and quoted text, so nothing hides in them."""
    bare = _STRINGS.sub("''", _COMMENTS.sub(" ", text))
    parts = re.split(r";|\n\s*/\s*(?:\n|$)", bare)
    return [p.strip() for p in parts if p.strip()]


def is_read_only(text: str, allowed: set[str] = READ_SQL) -> bool:
    stmts = _statements(text)
    if not stmts:
        return False
    for s in stmts:
        first = re.split(r"[\s(]", s, maxsplit=1)[0].lower()
        if first not in allowed:
            return False
        if re.search(r"\bfor\s+update\b", s, re.I) or re.search(r"\binto\s+\w", s, re.I) and first in {"select", "with"}:
            return False  # SELECT ... FOR UPDATE locks rows; SELECT ... INTO is PL/SQL
    return True


class Guard(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        args = context.message.arguments or {}
        key = name.lower().replace("-", "_")
        if key in SAFE_TOOLS:
            return await call_next(context)
        if key in SQL_TOOLS:
            arg, words = SQL_TOOLS[key]
            text = args.get(arg)       # only the statement itself is checked, not 'model' or 'execution_type'
            allowed = isinstance(text, str) and is_read_only(text, words)
        else:
            allowed = False            # skills_sync, annotation_generate, any tool we do not know
        if not allowed and get_http_headers().get(ALLOW_HEADER, "").strip().lower() != "true":
            raise ToolError(
                f"WRITE_BLOCKED: '{name}' would do more than read. This server is read-only unless the "
                "connection sends the header 'X-MCP-Allow-Destructive: true'. Next: ask the owner of the "
                "connection to allow it, or use a SELECT.")
        return await call_next(context)


class TokenGate:
    """Plain HTTP gate in front of everything except /health: a wrong or missing token gets a standard 401."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        given = [auth[7:] if auth.lower().startswith("bearer ") else "", headers.get("x-mcp-token", "")]
        if any(g and hmac.compare_digest(g.encode(), self.token.encode()) for g in given):
            return await self.app(scope, receive, send)
        body = b'{"error":"unauthorized","detail":"Send the server token as Authorization: Bearer <token> or X-MCP-Token."}'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="sqlcl-mcp"')]})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------- the server
def build_server(cfg: dict, client: Client | None = None) -> FastMCP:
    """One shared SQLcl session: the proxy always hands out the same connected client."""
    holder: dict = {"client": client}
    lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(_server):
        c = holder["client"] or Client(StdioTransport(cfg["command"], cfg["args"], env=dict(os.environ)))
        log.info("starting SQLcl: %s %s", cfg["command"], " ".join(cfg["args"]))
        try:
            await asyncio.wait_for(c.__aenter__(), cfg["startup_timeout"])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not start SQLcl ({cfg['command']} {' '.join(cfg['args'])}): {exc!r}. Check that Java 17+ "
                "is on PATH (java -version) and that SQLCL_COMMAND points to sql.exe or sql.bat.") from exc
        holder["client"] = c
        tools = [t.name for t in await c.list_tools()]
        log.info("SQLcl is up. Tools: %s", ", ".join(tools))
        try:
            yield
        finally:
            await c.__aexit__(None, None, None)

    mcp = FastMCP("sqlcl-http", version=__version__, lifespan=lifespan, middleware=[Guard()])

    def shared():
        return holder["client"]
    mcp.add_provider(ProxyProvider(shared, cache_ttl=0))

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):
        up = holder["client"] is not None and holder["client"].is_connected()
        return JSONResponse({"status": "ok" if up else "starting"}, status_code=200 if up else 503)

    mcp._sqlcl_lock = lock  # kept for tests / future serialisation
    return mcp


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    cfg = _settings()
    if "--list-tools" in argv:
        async def show():
            async with Client(StdioTransport(cfg["command"], cfg["args"], env=dict(os.environ))) as c:
                for t in await c.list_tools():
                    print(f"- {t.name}: {(t.description or '').strip().splitlines()[0] if t.description else ''}")
                    print(f"    arguments: {list(((getattr(t, 'input_schema', None) or t.inputSchema) or {}).get('properties', {}))}")
        asyncio.run(show())
        return 0
    if cfg["host"] not in LOOPBACK and not cfg["token"]:
        print("REFUSED: SQLCL_MCP_HOST is not local, and SQLCL_MCP_TOKEN is not set. This server uses a saved "
              "database login, so it must not listen on the network without a token.", file=sys.stderr)
        return 2
    mcp = build_server(cfg)
    log.info("Streamable HTTP on http://%s:%s/mcp  (read-only unless X-MCP-Allow-Destructive: true)",
             cfg["host"], cfg["port"])
    gate = [StarletteMiddleware(TokenGate, token=cfg["token"])] if cfg["token"] else None
    mcp.run(transport="http", host=cfg["host"], port=cfg["port"], show_banner=False, middleware=gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
