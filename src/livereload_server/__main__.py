"""Livereload server entry-point."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from watchfiles import awatch

from . import LiveReloadingStaticServer

logger = logging.getLogger(__name__)


async def amain(path_to_serve: Path) -> None:
    server = LiveReloadingStaticServer(path_to_serve, host="localhost", port=8000)
    async with server:
        try:
            async for _ in awatch(server.path_to_serve):
                await server.reload("*")
        except asyncio.CancelledError:
            logger.info("Recieved a cancellation!")


def _main(prog: str) -> None:
    parser = argparse.ArgumentParser(prog)
    parser.add_argument(
        "path_to_serve",
        type=Path,
        help="The path to watch and serve files from.",
    )
    args = parser.parse_args()

    if not args.path_to_serve.is_dir():
        raise ValueError(f"{args.path_to_serve} is not a directory!")

    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(amain(args.path_to_serve))


def main() -> None:
    _main("livereload-server")


if __name__ == "__main__":
    _main("python -m livereload_server")
