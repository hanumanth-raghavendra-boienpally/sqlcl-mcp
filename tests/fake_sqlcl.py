"""A stand-in for `sql -mcp`: the same tool names and argument names as the real SQLcl 26.x (taken from its
--list-tools output), over stdio. It remembers which connection is open."""
from fastmcp import FastMCP

mcp = FastMCP("fake-sqlcl")
state = {"conn": None, "ran": []}


@mcp.tool(name="connections_list")
def connections_list(name: str = "", model: str = "") -> str:
    return "vis_oracle"


@mcp.tool(name="connect")
def connect(connection_name: str, model: str = "") -> str:
    state["conn"] = connection_name
    return f"connected {connection_name}"


@mcp.tool(name="sql_run")
def sql_run(sql: str, model: str = "", execution_type: str = "SYNCHRONOUS") -> str:
    state["ran"].append(sql)
    return f"[{state['conn']}] ran: {sql}"


@mcp.tool(name="sqlcl_run")
def sqlcl_run(sqlcl: str, model: str = "", execution_type: str = "SYNCHRONOUS") -> str:
    state["ran"].append(sqlcl)
    return f"[{state['conn']}] sqlcl: {sqlcl}"


@mcp.tool(name="schema_information")
def schema_information(schema: str = "", model: str = "") -> str:
    return f"[{state['conn']}] schema {schema}"


@mcp.tool(name="skills_sync")
def skills_sync(force: bool = False, model: str = "") -> str:
    state["ran"].append("skills_sync")
    return "synced"


@mcp.tool(name="annotation_generate")
def annotation_generate(operation: str = "preview", model: str = "") -> str:
    state["ran"].append("annotation")
    return "annotated"


@mcp.tool(name="mystery_tool")
def mystery_tool() -> str:
    return "ran"


@mcp.tool(name="disconnect")
def disconnect(model: str = "") -> str:
    state["conn"] = None
    return "disconnected"


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
