#!/usr/bin/env bash
# Container start-up: save the Oracle connection for SQLcl, prove it works, then start the HTTP wrapper.
# Settings (Container Apps env vars; the password and the token come from secrets):
#   ORACLE_USER  ORACLE_PASSWORD  ORACLE_HOST  ORACLE_PORT  ORACLE_SID      (all required)
#   ORACLE_CONNECTION_NAME (default vis_oracle)   SQLCL_MCP_TOKEN (required: the server listens on the network)
set -u

fail() { echo "STARTUP FAILED: $*" >&2; exit 1; }

for v in ORACLE_USER ORACLE_PASSWORD ORACLE_HOST ORACLE_PORT ORACLE_SID SQLCL_MCP_TOKEN; do
  [ -n "${!v:-}" ] || fail "$v is not set."
done
NAME="${ORACLE_CONNECTION_NAME:-vis_oracle}"
SQL="${SQLCL_COMMAND:-sql}"

case "$ORACLE_PASSWORD" in
  *\"*|*$'\n'*) fail "ORACLE_PASSWORD contains a double quote or a line break, which this script cannot pass to SQLcl." ;;
esac
case "$ORACLE_PORT" in *[!0-9]*) fail "ORACLE_PORT must be a number." ;; esac

# The password goes to SQLcl through standard input, never on a command line (those show up in process lists).
OUT="$(printf '%s\n' \
  "connect -save $NAME -savepwd $ORACLE_USER/\"$ORACLE_PASSWORD\"@$ORACLE_HOST:$ORACLE_PORT:$ORACLE_SID" \
  "select 'CONNECTION_OK' as status from dual;" \
  "exit" | "$SQL" -S /nolog 2>&1)"

if ! printf '%s' "$OUT" | grep -q "CONNECTION_OK"; then
  # Show Oracle's error lines only; the password is not in SQLcl's output, and is masked here as well.
  MASKED="${OUT//"$ORACLE_PASSWORD"/********}"
  printf '%s\n' "$MASKED" | grep -v "^[[:space:]]*$" | head -n 8 >&2
  fail "could not connect to $ORACLE_HOST:$ORACLE_PORT as $ORACLE_USER (SID $ORACLE_SID). Check the bridge address, the login, and the secret."
fi
echo "Saved connection '$NAME' and verified it ($ORACLE_USER@$ORACLE_HOST:$ORACLE_PORT:$ORACLE_SID)."

unset ORACLE_PASSWORD
exec "${SQLCL_PYTHON:-/opt/venv/bin/python}" "${SQLCL_APP:-/app/sqlcl_http.py}"
