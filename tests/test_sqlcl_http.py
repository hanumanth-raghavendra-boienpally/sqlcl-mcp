import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import sqlcl_http as w

ROOT = Path(__file__).resolve().parents[1]
FAKE = str(Path(__file__).with_name("fake_sqlcl.py"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    def __init__(self, **env):
        self.port = free_port()
        e = {**{k: v for k, v in os.environ.items() if not k.startswith("SQLCL_")},
             "SQLCL_COMMAND": sys.executable, "SQLCL_ARGS": FAKE, "SQLCL_MCP_PORT": str(self.port), **env}
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "sqlcl_http.py")], cwd=ROOT, env=e,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def __enter__(self):
        for _ in range(300):
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.__exit__()
        raise AssertionError(self.err)

    def __exit__(self, *a):
        self.proc.terminate()
        _o, self.err = self.proc.communicate(timeout=20)

    def call(self, tool, args=None, headers=None, expect_error=False):
        async def go():
            t = StreamableHttpTransport(f"http://127.0.0.1:{self.port}/mcp", headers=headers or {})
            async with Client(t) as c:
                return await c.call_tool(tool, args or {}, raise_on_error=False)
        res = asyncio.run(asyncio.wait_for(go(), 60))           # a hang becomes a failure, not a stuck run
        text = "".join(getattr(b, "text", "") for b in res.content)
        assert res.is_error == expect_error, text
        return text


def test_tools_are_passed_through_and_health_is_ok():
    with Running() as srv:
        async def names():
            async with Client(StreamableHttpTransport(f"http://127.0.0.1:{srv.port}/mcp")) as c:
                return {t.name for t in await c.list_tools()}
        assert asyncio.run(names()) == {"connections_list", "connect", "sql_run", "sqlcl_run", "disconnect",
                                        "schema_information", "skills_sync", "annotation_generate", "mystery_tool"}
        assert httpx.get(f"http://127.0.0.1:{srv.port}/health").json() == {"status": "ok"}


def test_one_login_is_kept_between_calls_and_between_clients():
    with Running() as srv:
        srv.call("connect", {"connection_name": "vis_oracle"})
        assert "[vis_oracle]" in srv.call("sql_run", {"sql": "select sysdate from dual"})   # a different HTTP client


@pytest.mark.parametrize("sql", ["select 1 from dual", "SELECT * FROM t WHERE a = 'drop table x'",
                                 "with a as (select 1 x from dual) select * from a;",
                                 "-- hi\nselect 1 from dual", "describe emp", "show user"])
def test_reads_are_allowed(sql):
    with Running() as srv:
        assert "ran:" in srv.call("sql_run", {"sql": sql})


@pytest.mark.parametrize("sql", ["delete from t", "drop table t", "insert into t values (1)", "update t set a=1",
                                 "select 1 from dual; delete from t", "/* x */ delete from t",
                                 "select * from t for update", "begin null; end;", "commit", "truncate table t",
                                 "grant dba to x", "create table t (a int)", "merge into t using s on (1=1)", ""])
def test_writes_are_blocked_by_default_and_allowed_with_the_header(sql):
    with Running() as srv:
        assert "WRITE_BLOCKED" in srv.call("sql_run", {"sql": sql}, expect_error=True)
        if sql:
            assert "ran:" in srv.call("sql_run", {"sql": sql}, headers={"X-MCP-Allow-Destructive": "true"})


def test_sqlcl_commands():
    with Running() as srv:
        assert "sqlcl:" in srv.call("sqlcl_run", {"sqlcl": "desc emp"})
        for cmd in ("host dir", "@script.sql", "spool out.txt", "script x.js"):
            assert "WRITE_BLOCKED" in srv.call("sqlcl_run", {"sqlcl": cmd}, expect_error=True)
        assert "sqlcl:" in srv.call("sqlcl_run", {"sqlcl": "host dir"}, headers={"X-MCP-Allow-Destructive": "true"})


def test_a_false_header_does_not_unlock():
    with Running() as srv:
        assert "WRITE_BLOCKED" in srv.call("sql_run", {"sql": "delete from t"},
                                           headers={"X-MCP-Allow-Destructive": "false"}, expect_error=True)


def test_non_local_host_needs_a_token():
    r = subprocess.run([sys.executable, str(ROOT / "sqlcl_http.py")], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "SQLCL_MCP_HOST": "0.0.0.0", "SQLCL_MCP_TOKEN": ""}, timeout=60)
    assert r.returncode == 2 and "REFUSED" in r.stderr


def test_token_is_enforced_when_set():
    tok = "a-long-enough-token-1234567890"
    with Running(SQLCL_MCP_TOKEN=tok) as srv:
        url = f"http://127.0.0.1:{srv.port}/mcp"
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
        base = {"Accept": "application/json, text/event-stream"}

        def post(**h):
            return httpx.post(url, json=init, headers={**base, **h}, timeout=15)
        r = post()
        assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"] and tok not in r.text
        assert post(Authorization="Bearer wrong-token-0000000000000").status_code == 401
        assert post(Authorization=f"Bearer {tok}").status_code == 200
        assert post(**{"X-MCP-Token": tok}).status_code == 200
        assert httpx.get(f"http://127.0.0.1:{srv.port}/health", timeout=5).status_code == 200   # probes need no token
        assert "ran:" in srv.call("sql_run", {"sql": "select 1 from dual"}, headers={"Authorization": f"Bearer {tok}"})


def test_bad_command_gives_a_clear_error():
    r = subprocess.run([sys.executable, str(ROOT / "sqlcl_http.py")], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "SQLCL_COMMAND": "definitely-not-a-program", "SQLCL_STARTUP_TIMEOUT": "10",
                            "SQLCL_MCP_PORT": str(free_port())}, timeout=60)
    assert r.returncode != 0 and "Could not start SQLcl" in r.stderr and "java -version" in r.stderr


def test_list_tools_mode():
    r = subprocess.run([sys.executable, str(ROOT / "sqlcl_http.py"), "--list-tools"], cwd=ROOT, capture_output=True,
                       text=True, env={**os.environ, "SQLCL_COMMAND": sys.executable, "SQLCL_ARGS": FAKE}, timeout=60)
    assert r.returncode == 0 and "- sql_run" in r.stdout and "'sql', 'model', 'execution_type'" in r.stdout


def test_model_and_execution_type_arguments_do_not_matter():
    with Running() as srv:
        assert "ran:" in srv.call("sql_run", {"sql": "select 1 from dual", "model": "gpt", "execution_type": "ASYNCHRONOUS"})
        assert "WRITE_BLOCKED" in srv.call("sql_run", {"sql": "delete from t", "model": "select 1"}, expect_error=True)
        assert "WRITE_BLOCKED" in srv.call("sql_run", {"model": "x"}, expect_error=True)      # no sql at all


def test_tools_that_can_change_things_need_the_header_and_unknown_tools_too():
    with Running() as srv:
        assert "schema" in srv.call("schema_information", {})
        assert "vis_oracle" in srv.call("connections_list", {})
        for tool in ("skills_sync", "annotation_generate", "mystery_tool"):
            assert "WRITE_BLOCKED" in srv.call(tool, {}, expect_error=True)
            srv.call(tool, {}, headers={"X-MCP-Allow-Destructive": "true"})


def test_connmgr_changes_need_the_header():
    with Running() as srv:
        assert "WRITE_BLOCKED" in srv.call("sqlcl_run", {"sqlcl": "connmgr delete vis_oracle"}, expect_error=True)


def test_read_only_helper():
    assert w.is_read_only("select 1 from dual") and not w.is_read_only("select * into x from y")
    assert not w.is_read_only("") and not w.is_read_only("  ;  ")
