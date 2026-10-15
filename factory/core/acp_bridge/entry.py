"""CLI entry point for the factory ACP bridge.

Usage::

    python -m factory.core.acp_bridge.entry
    # or via uv tool:
    factory-acp
"""

import asyncio
import logging
import sys
from pathlib import Path

import acp

from .server import FactoryACPAgent


def _setup_logging() -> None:
    """Route all logging to stderr so stdout stays clean for ACP stdio."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _run() -> None:
    """Run the ACP server."""
    agent = FactoryACPAgent()
    try:
        await acp.run_agent(agent, use_unstable_protocol=True)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logging.getLogger(__name__).exception("ACP agent crashed")
        sys.exit(1)


def main() -> None:
    """Entry point: configure logging and run ACP server."""
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting factory ACP bridge")

    project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
