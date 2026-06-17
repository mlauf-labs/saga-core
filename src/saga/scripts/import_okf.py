"""Thin REST client: upload an OKF bundle to /import/okf.

Usage:
    saga-import-okf --token <token> --bundle ./bundle.tar.gz
    saga-import-okf --token <token> --dir ./extracted-bundle   # tars the dir first
"""

from __future__ import annotations

import argparse
import asyncio
import io
import tarfile
from pathlib import Path

import httpx

from saga.core.logging import configure_logging, get_logger

_log = get_logger("saga.import.client")


def _tar_directory(directory: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(directory, arcname=directory.name)
    return buf.getvalue()


async def _run(*, base_url: str, token: str, bundle: Path | None, directory: Path | None) -> None:
    if bundle is not None:
        data = bundle.read_bytes()
    elif directory is not None:
        data = _tar_directory(directory)
    else:  # pragma: no cover - argparse guarantees one is set
        raise SystemExit("Provide --bundle or --dir.")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=300.0) as client:
        response = await client.post(
            "/import/okf", files={"file": ("bundle.tar.gz", data, "application/gzip")}
        )
        response.raise_for_status()
        _log.info("okf_import_done", summary=response.json())


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description="Import an OKF bundle into SAGA.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle", type=Path, help="A .tar.gz bundle to upload.")
    group.add_argument("--dir", type=Path, dest="directory", help="A bundle directory to tar.")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(
        _run(base_url=args.base_url, token=args.token, bundle=args.bundle, directory=args.directory)
    )


if __name__ == "__main__":
    main()
