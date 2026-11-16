# Deploying the SQLcl MCP on Azure Container Apps, and adding it to the workflow designer

What runs: one container with Java 21, Oracle SQLcl (unmodified, Oracle Free Use license, internal use), and
`sqlcl_http.py`, which serves SQLcl's MCP tools as Streamable HTTP at `/mcp`. `/health` answers `{"status":"ok"}`.

**Read this first**
- **Exactly one replica.** SQLcl keeps one database login per process, so `--min-replicas 1 --max-replicas 1`.
- **One shared login.** Everyone who uses the agent uses the same Oracle account (`ORACLE_USER`). Use a read-only
  account unless writes are really intended.
- **Read-only by default.** Writes are refused unless the designer entry sends `X-MCP-Allow-Destructive: true`.
  SQLcl's AUTOCOMMIT is ON, so a write is permanent at once.
- **Needs a network path to the database.** The app must reach the bridge address (plain TCP forward of the Oracle
  port; an HTTP-only gateway will not work).
- **The password is a server secret** (Container Apps secret), unlike our other connectors. The server token
  (`SQLCL_MCP_TOKEN`) is required because the server listens on the network.
- Not tested here: the Docker build and the Azure run (no Docker in the authoring environment). Check each step below.

## Settings

| Setting | Meaning | Where |
|---|---|---|
| `ORACLE_USER` | Oracle login | env var |
| `ORACLE_PASSWORD` | its password (no `"` or line breaks) | **secret** |
| `ORACLE_HOST`, `ORACLE_PORT` | the bridge address and port | env var |
| `ORACLE_SID` | database SID (`EBSVIS` on the direct connection) | env var |
| `SQLCL_MCP_TOKEN` | server token, at least 24 characters | **secret** |
| `ORACLE_CONNECTION_NAME` | name SQLcl saves it under (default `vis_oracle`) | env var, optional |

At start the container saves the connection, runs `select 'CONNECTION_OK' from dual`, and **stops with a clear log
line if that fails** (wrong host, login or secret). It never prints the password.

## Steps (Command Prompt, in the `sqlcl_http` folder, after `az login`)

1. **Build the image in the registry:**
   ```cmd
   az acr build --registry <acr-name> --image sqlcl-mcp:1.0.0 .
   ```
   If the build cannot reach Oracle's download address, put `sqlcl-latest.zip` (from Oracle's SQLcl download page)
   in this folder and build with `--build-arg SQLCL_URL=local`.

2. **Make a token** (any 32+ character random string), for example:
   ```cmd
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. **Create the container app.** Fill the `<...>` values. If the password contains `&`, `^`, `%` or `!`, create the
   app without `ORACLE_PASSWORD` and add that secret in the portal instead (app → Settings → Secrets).
   ```cmd
   az containerapp create --name sqlcl-mcp --resource-group <resource-group> --environment <containerapps-environment> --image <acr-name>.azurecr.io/sqlcl-mcp:1.0.0 --registry-server <acr-name>.azurecr.io --registry-identity system --ingress external --target-port 8770 --min-replicas 1 --max-replicas 1 --secrets oracle-password="<password>" mcp-token="<token from step 2>" --env-vars ORACLE_USER=<user> ORACLE_PASSWORD=secretref:oracle-password SQLCL_MCP_TOKEN=secretref:mcp-token ORACLE_HOST=<bridge-host> ORACLE_PORT=<bridge-port> ORACLE_SID=<sid>
   ```

4. **Health probes** (portal: the app → Containers → Health probes): liveness and readiness `HTTP GET /health`,
   port `8770`; set the start-up delay to at least 60 seconds (Java takes a few seconds to start).

5. **Check it.** Read the app's address, then call it:
   ```cmd
   az containerapp show --name sqlcl-mcp --resource-group <resource-group> --query properties.configuration.ingress.fqdn -o tsv
   curl https://<fqdn>/health
   ```
   `{"status":"ok"}` means SQLcl started and the login worked. If the app keeps restarting, open **Log stream**: the
   line `STARTUP FAILED: ...` says what is wrong.
   If the bridge needs an allow-list, give its owner the app's outbound IP addresses
   (`az containerapp env show ... --query properties.staticIp`, or the environment's NAT gateway address).

## Workflow designer (SUPERADMIN: `PUT /api/global-mcp-registry`)

```json
"sqlcl-mcp": {
  "id": "sqlcl-mcp",
  "name": "Oracle SQLcl",
  "description": "Runs SQL against the Oracle database through Oracle's SQLcl MCP server. Read-only unless allowed.",
  "license": "Oracle Free Use (SQLcl)",
  "vendor": "Winfo Solutions",
  "auth_type": "headers",
  "categories": ["database", "oracle"],
  "mcp_config_template": {
    "transport": "streamable_http",
    "url": "https://<fqdn>/mcp",
    "headers": {"Authorization": "", "X-MCP-Allow-Destructive": "false"}
  }
}
```

In the agent's MCP dialog set `Authorization` to `Bearer <token from step 2>` (pick it from Key Vault if stored there)
and leave `X-MCP-Allow-Destructive` as `false` unless the agent may write. In the agent's instructions say:
"Use the Oracle SQLcl tools. First call `connect` with the connection name `vis_oracle`, then use `sql_run`."

## Test (from VS Code, with the real address)

```json
{ "mcpServers": { "sqlcl-azure": { "type": "http", "url": "https://<fqdn>/mcp",
  "headers": { "Authorization": "Bearer <token>", "X-MCP-Allow-Destructive": "false" } } } }
```
Ask: "Connect to vis_oracle and run select sysdate from dual", then "Run delete from dual" (must say `WRITE_BLOCKED`).
