import asyncio

import docker

from .server import run_stdio
from .settings import ServerSettings


def main():
    """Run the server sourcing configuration from environment variables."""
    asyncio.run(run_stdio(ServerSettings(), docker.from_env()))


# Optionally expose other important items at package level
__all__ = ["main", "run_stdio", "ServerSettings"]

