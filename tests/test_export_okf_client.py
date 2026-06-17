from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

from saga.scripts.export_okf import download_bundle, extract_bundle


def _fake_targz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"# Index\n"
        info = tarfile.TarInfo("okf-saga-x/index.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def test_download_bundle_writes_file(tmp_path: Path) -> None:
    payload = _fake_targz()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/export/okf"
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, content=payload)

    out = tmp_path / "bundle.tar.gz"
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://x", headers={"Authorization": "Bearer t"}
    ) as client:
        await download_bundle(client, out=out, with_originals=False)
    assert out.read_bytes() == payload


async def test_download_bundle_sends_with_originals_param(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["with_originals"] = request.url.params["with_originals"]
        return httpx.Response(200, content=_fake_targz())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        await download_bundle(client, out=tmp_path / "b.tar.gz", with_originals=True)
    assert seen["with_originals"] == "true"


def test_extract_bundle_unpacks(tmp_path: Path) -> None:
    archive = tmp_path / "b.tar.gz"
    archive.write_bytes(_fake_targz())
    into = tmp_path / "out"
    extract_bundle(archive, into)
    assert (into / "okf-saga-x" / "index.md").read_bytes() == b"# Index\n"
