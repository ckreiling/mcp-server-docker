from mcp.server.fastmcp import FastMCP

# Initialize FastMCP app
app = FastMCP("docker-server")


@app.tool()
def hello_world(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"


def run_stdio():
    """Run the server on Standard I/O."""
    app.run("stdio")
