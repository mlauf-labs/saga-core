# OKF External Conformance & Consumption — Runbook

> **What this is.** A step-by-step procedure for **manually verifying** that a SAGA-exported
> **Open Knowledge Format (OKF)** bundle is consumed correctly by *external* OKF tooling —
> beyond the in-repo automated check. This is the human-driven half of roadmap **Track I**
> (the automated half is `tests/export/test_okf_conformance.py`, NFR-36).
>
> **Who runs it.** The repository owner / an operator with access to the test server. It needs
> the running SAGA stack and the external tools, so it cannot be run from CI or by an agent.

- **Related:** [OKF integration roadmap](okf-integration-roadmap.md) (Track I) ·
  [REST API → OKF interchange](api/rest-api.md) ·
  in-repo conformance test `tests/export/test_okf_conformance.py`
- **Test server:** Debian VM at `10.0.0.220` (see workspace `TESTSERVER.md`). API at
  `http://10.0.0.220:8000`, Swagger at `/docs`.

---

## 0. Why a manual run at all?

The in-repo test proves our bundle conforms to the OKF v0.1 *contract as we understand it*
(frontmatter keys, reserved `index.md`/`log.md`, ignorable machine extras). It cannot prove a
*real third-party OKF consumer* accepts the bundle — different parsers make different
assumptions. This runbook closes that gap by feeding a real bundle to real tools and recording
what happens.

OKF v0.1 is young; the exact external tools and their invocation **change**. Where this runbook
says *"confirm the current tool/version"*, check the tool's own docs first
(<https://openknowledgeformat.com>) — do not assume the commands below are evergreen.

---

## 1. Prerequisites

- The SAGA stack is **up** on the test server with a **representative corpus** ingested:
  several documents, a few nested folders, at least one document with extracted values and a
  note, and ideally some timeline events (so `log.md` is non-empty). A near-empty store is not
  a meaningful conformance test.
- An **API token** (one of `SAGA_API_TOKENS` from the server `.env`). Export over: it is bearer-auth
  protected like every endpoint.
- A workstation with `curl`, `tar`, and Python 3 (for `python -m http.server` / local checks).
- For the external tools: a browser; and (for the BigQuery step) a GCP project with BigQuery +
  a GCS bucket and `gcloud`/`bq` configured. The BigQuery step is **aspirational** — do it only
  if a GCP project is available.

---

## 2. Produce an OKF bundle

```bash
export SAGA=http://10.0.0.220:8000
export TOKEN=<one of SAGA_API_TOKENS>

# Pure-markdown bundle (what a generic OKF consumer sees).
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$SAGA/export/okf" -o okf-bundle.tar.gz

# Optionally, a bundle WITH originals colocated (larger; for tools that want the source files):
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$SAGA/export/okf?with_originals=true" -o okf-bundle-with-originals.tar.gz

mkdir -p okf-bundle && tar -xzf okf-bundle.tar.gz -C okf-bundle
ls -R okf-bundle | head -50
```

You should see a single root dir `okf-<store>-<timestamp>/` containing `index.md`, the
machine extras `saga-manifest.json` + `saga-events.jsonl`, and per-folder subdirectories with
`index.md`, optional `log.md`, and one concept `.md` per document.

---

## 3. Local sanity check (fast gate before the external tools)

Confirm the bundle matches the OKF v0.1 contract before spending time in external tools:

1. **Root overview** — `okf-<store>-*/index.md` opens with a `#` heading and bullet links to
   subfolders/`Unfiled`.
2. **Concept frontmatter** — pick any document `.md` (not `index.md`/`log.md`). It starts with a
   `---` YAML block that parses and has a non-empty `type` and a `title`. Foreign-origin extra
   keys (if any) appear as **top-level** keys.
3. **Reserved files** — `index.md` / `log.md` carry **no** frontmatter (plain markdown headings).
4. **Machine extras** — `saga-manifest.json` is valid JSON; `saga-events.jsonl` is one JSON
   object per line. A generic OKF consumer must simply **ignore** these (they are the only
   non-markdown files in a pure bundle).

A quick scripted version of the same checks (mirrors the in-repo test logic):

```bash
python - <<'PY'
import pathlib, yaml, json
root = next(pathlib.Path("okf-bundle").iterdir())
reserved, concepts, extras = {"index.md", "log.md"}, 0, set()
for p in root.rglob("*"):
    if p.is_dir():
        continue
    if p.suffix != ".md":
        extras.add(p.name); continue
    if p.name in reserved:
        assert not p.read_text(encoding="utf-8").startswith("---\n"), f"{p} has frontmatter"
        continue
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{p} missing frontmatter"
    fm = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
    assert isinstance(fm, dict) and str(fm.get("type", "")).strip(), f"{p} bad 'type'"
    assert "title" in fm, f"{p} missing 'title'"
    concepts += 1
assert extras <= {"saga-manifest.json", "saga-events.jsonl"}, f"unexpected extras: {extras}"
print(f"OK: {concepts} concept files; non-markdown extras = {sorted(extras)}")
PY
```

If this fails, **stop** — fix the export (it's our bug), do not proceed to the external tools.

---

## 4. External consumer A — Google static-HTML knowledge-graph visualizer

The OKF ecosystem includes a static HTML visualizer that renders an OKF bundle as a browsable
knowledge graph (folders/concepts as nodes, links as edges). It runs entirely client-side.

1. **Obtain the visualizer** — confirm the current distribution from
   <https://openknowledgeformat.com> (a downloadable static-HTML bundle / a GitHub release).
   Note its version in the results table.
2. **Point it at the bundle.** Serve the extracted bundle over HTTP so relative links resolve:
   ```bash
   (cd okf-bundle && python -m http.server 8910)
   ```
   Open the visualizer in a browser and give it the bundle root URL
   (`http://localhost:8910/okf-<store>-<timestamp>/`), per the tool's instructions.
3. **Verify:**
   - the **folder tree** renders (root → subfolders), matching SAGA's hierarchy;
   - each **document concept** appears as a node with its `title`/`type`;
   - **links** between `index.md` and concepts resolve (no 404s in the browser console);
   - the visualizer does **not** choke on `saga-manifest.json` / `saga-events.jsonl` (it should
     ignore non-markdown files);
   - `log.md` change-logs render if the tool surfaces them.
4. Capture a screenshot and any console errors for the results record.

**Pass:** the graph renders the corpus faithfully with no parse/link errors. **Fail:** record
the exact error and which file/frontmatter key triggered it.

---

## 5. External consumer B — BigQuery Knowledge Catalog ingestion (aspirational)

Only if a GCP project is available. Goal: load the bundle into BigQuery via the OKF→catalog
loader and query it.

1. **Confirm the loader** — check <https://openknowledgeformat.com> for the current
   BigQuery/Knowledge-Catalog ingestion path (a published loader script or a documented schema).
   Record the tool + version.
2. **Stage the bundle** in GCS:
   ```bash
   gsutil -m cp -r okf-bundle/okf-* gs://<your-bucket>/okf-test/
   ```
3. **Run the loader** per its docs (it parses concept frontmatter + bodies into catalog rows).
4. **Verify** with a query, e.g. that the document count and `type` distribution match the
   source store:
   ```sql
   SELECT type, COUNT(*) AS n
   FROM `<project>.<dataset>.<okf_concepts_table>`
   GROUP BY type ORDER BY n DESC;
   ```
   Cross-check `n` and types against `GET /documents` on the source store.

**Pass:** row counts + `type` distribution match the source. **Fail:** record the loader error
or the mismatch.

---

## 6. Record the results

Append a dated entry to the table below (commit it). This is the audit trail Track I asks for.

| Date | Bundle (store / #docs) | Tool + version | Result | Notes / issues filed |
|------|------------------------|----------------|--------|----------------------|
| _e.g._ 2026-06-18 | saga / 42 docs | OKF HTML viz vX.Y | ✅ renders | machine extras ignored as expected |
|  |  |  |  |  |

If a tool rejects the bundle, file an issue in `saga-core` describing the failing file +
frontmatter key, link it here, and (if it's our contract bug) add a regression case to
`tests/export/test_okf_conformance.py` so it's caught automatically next time.

---

## 7. Cleanup

```bash
# stop the local server (Ctrl-C), then:
rm -rf okf-bundle okf-bundle*.tar.gz
gsutil -m rm -r gs://<your-bucket>/okf-test/   # if the BigQuery step ran
```

No server-side state is created by an export, so nothing to clean up on `10.0.0.220`.
