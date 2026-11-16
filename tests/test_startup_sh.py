"""The container start-up script, run with a stand-in `sql` (needs bash: skipped on plain Windows)."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs bash")

FAKE_SQL = """#!/usr/bin/env bash
echo "$@" > "$FAKE_DIR/args.txt"
cat > "$FAKE_DIR/stdin.txt"
if grep -q 'GOODPASS' "$FAKE_DIR/stdin.txt"; then echo CONNECTION_OK; else echo "ORA-01017: invalid username/password; logon denied"; fi
"""
ENV = {"ORACLE_USER": "readvis", "ORACLE_PASSWORD": "GOODPASS", "ORACLE_HOST": "bridge.internal", "ORACLE_PORT": "1521",
       "ORACLE_SID": "EBSVIS", "SQLCL_MCP_TOKEN": "a-long-enough-token-1234567890"}


def run(tmp_path, **over):
    sql = tmp_path / "sql"
    sql.write_text(FAKE_SQL)
    sql.chmod(sql.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": os.environ["PATH"], "FAKE_DIR": str(tmp_path), "SQLCL_COMMAND": str(sql),
           "SQLCL_PYTHON": "/bin/echo", "SQLCL_APP": "WRAPPER-STARTED", **ENV, **over}
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run([BASH, str(ROOT / "startup.sh")], env=env, capture_output=True, text=True, timeout=30)


def test_saves_the_connection_checks_it_and_starts_the_wrapper(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 0 and "WRAPPER-STARTED" in r.stdout and "verified" in r.stdout
    stdin = (tmp_path / "stdin.txt").read_text()
    assert 'connect -save vis_oracle -savepwd readvis/"GOODPASS"@bridge.internal:1521:EBSVIS' in stdin
    assert "GOODPASS" not in (tmp_path / "args.txt").read_text()          # never on a command line
    assert "GOODPASS" not in r.stdout + r.stderr


def test_wrong_password_stops_with_a_clear_error_and_never_prints_it(tmp_path):
    r = run(tmp_path, ORACLE_PASSWORD="BADPASS")
    assert r.returncode == 1 and "STARTUP FAILED" in r.stderr and "ORA-01017" in r.stderr
    assert "BADPASS" not in r.stdout + r.stderr and "WRAPPER-STARTED" not in r.stdout


@pytest.mark.parametrize("missing", ["ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_HOST", "ORACLE_PORT", "ORACLE_SID",
                                     "SQLCL_MCP_TOKEN"])
def test_every_required_setting_is_checked(tmp_path, missing):
    r = run(tmp_path, **{missing: None})
    assert r.returncode == 1 and f"{missing} is not set" in r.stderr


def test_unsafe_password_and_port_are_refused(tmp_path):
    assert "double quote" in run(tmp_path, ORACLE_PASSWORD='bad"pass').stderr
    assert "must be a number" in run(tmp_path, ORACLE_PORT="15;21").stderr
