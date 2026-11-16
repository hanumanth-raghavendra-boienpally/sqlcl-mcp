# Hand-off for deployment (read this before converting to the company sample format)

**What it is:** an MCP server in Python, FastMCP, transport **http** (Streamable HTTP), that serves Oracle's SQLcl MCP
server to the workflow designer. Read `README.md` for how it works.

| Question | Answer |
|---|---|
| 1. Framework | **FastMCP** (`fastmcp>=4.1,<5`) |
| 2. Transport | **http** (Streamable HTTP). MCP endpoint `/mcp`, health `/health`, port **8770** |
| 3. `requirements.txt` | **Yes**: `requirements.txt` (runtime only: `fastmcp`, `httpx`). Test tools are in `requirements-dev.txt`. |
| 4. `.env` | **No `.env` file, but it needs these settings** (see `.env.example`): 2 secrets, 4 variables, 1 optional |

**Settings to place in GitHub (and reference in the yaml)**
- **Secrets:** `ORACLE_PASSWORD`, `SQLCL_MCP_TOKEN`
- **Variables:** `ORACLE_USER`, `ORACLE_HOST`, `ORACLE_PORT`, `ORACLE_SID`, and optionally `ORACLE_CONNECTION_NAME`

**This MCP is different from a plain Python MCP. Please keep these when converting:**
1. **It needs Java 21 and Oracle SQLcl inside the container.** The `Dockerfile` here installs both (base image
   `eclipse-temurin:21-jre`, SQLcl downloaded unmodified from Oracle). If the sample's Dockerfile is Python-only, keep
   the Java and SQLcl lines from this `Dockerfile`.
2. **Start command is `/app/startup.sh`**, not `python server.py`. It saves the Oracle connection from the settings above,
   checks it, and then starts `sqlcl_http.py`. It stops with a clear error if the database cannot be reached.
3. **Exactly one replica** (min 1, max 1): SQLcl keeps one database login per process.
4. **Port 8770**; probes: `GET /health` (no token needed). Allow about 60 seconds for start-up (Java).
5. The server **refuses to listen on the network without `SQLCL_MCP_TOKEN`**.
6. **Network:** the container must reach the database bridge (plain TCP forward of the Oracle port).

**Registering in the workflow designer:** transport `streamable_http`, URL `https://<fqdn>/mcp`, header
`Authorization: Bearer <SQLCL_MCP_TOKEN>`, and `X-MCP-Allow-Destructive: false` (set `true` only if the agent may write).

`DEPLOYMENT.md` holds manual `az` commands; use them only if the company workflow is not used.

**Tested:** locally on Windows against Oracle 19c with VS Code Copilot (connect, select, session kept, writes blocked,
token gate); 41 automated tests. **Not tested:** the Docker build, Azure, the bridge, writes.
