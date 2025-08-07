from .server import run_stdio


def main():
    """Run the server."""
    run_stdio()


# Optionally expose other important items at package level
__all__ = ["main", "run_stdio"]

