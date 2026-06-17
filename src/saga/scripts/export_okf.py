"""Thin REST client: download the OKF bundle from /export/okf and save it.

Usage:
    saga-export-okf --base-url http://localhost:8000 --token <token> --out bundle.tar.gz
    saga-export-okf --token <token> --with-originals --extract ./bundle
"""

from __future__ import annotations

import argparse
import asyncio
import tarfile
from pathlib import Path

import httpx

from saga.core.logging import configure_logging, get_logger

_log = get_logger("saga.export.client")


async def download_bundle(client: httpx.AsyncClient, *, out: Path, with_originals: bool) -> None:
    """GET /export/okf and write the .tar.gz to *out*."""
    params = {"with_originals": str(with_originals).lower()}
    response = await client.get("/export/okf", params=params)
    response.raise_for_status()
    out.write_bytes(response.content)
    _log.info("okf_bundle_saved", out=str(out), bytes=len(response.content))


def extract_bundle(archive: Path, into: Path) -> None:
    """Extract the OKF .tar.gz at *archive* into the directory *into*."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as tar:
        tar.extractall(into, filter="data")
    _log.info("okf_bundle_extracted", archive=str(archive), into=str(into))


async def _run(
    *, base_url: str, token: str, out: Path, with_originals: bool, extract: Path | None
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=300.0) as client:
        await download_bundle(client, out=out, with_originals=with_originals)
    if extract is not None:
        extract_bundle(out, extract)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description="Download the SAGA archive as an OKF bundle.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--out", type=Path, default=Path("okf-bundle.tar.gz"))
    parser.add_argument("--with-originals", action="store_true")
    parser.add_argument(
        "--extract", type=Path, default=None, help="Also extract the bundle into this directory."
    )
    args = parser.parse_args()
    configure_logging()
    asyncio.run(
        _run(
            base_url=args.base_url,
            token=args.token,
            out=args.out,
            with_originals=args.with_originals,
            extract=args.extract,
        )
    )


if __name__ == "__main__":
    main()
