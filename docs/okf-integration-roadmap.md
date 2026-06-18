# SAGA ↔ OKF Integration — Open Items & Roadmap

> **Living tracker.** Captures what has landed and what is still to do to bring SAGA and the
> **Open Knowledge Format (OKF)** together across the ecosystem (saga-core, saga-backup,
> saga-ui, saga-importer). This is a planning doc, not a spec — each remaining Track that says
> "own spec" goes through the normal brainstorm → spec → plan → implement cycle.

- **Created:** 2026-06-17 · **Last updated:** 2026-06-18 (Track E/H merged; Track I local
  conformance + timeline-emission test debt landed)
- **Scope note:** OKF is an **interchange/serialization format at the boundary**, never the
  database storage model. Postgres stays the system of record; OpenSearch stays a rebuildable
  projection; an OKF bundle is a *third* projection (export) and an *import* source.

---

## 1. Decisions already made (do not re-litigate)

- OKF = interchange/serialization layer (export + import), **not** a DB schema change.
- **Export/import live in `saga-core` as REST endpoints** (`GET /export/okf`,
  `POST /import/okf`) + thin clients, **not** in `saga-backup`. This supersedes the original
  plan to replace `saga-backup`'s `export` command. (Consequence: saga-backup's legacy
  `export` is untouched — see Track H for whether to retire it.)
- Originals: **`--with-originals` flag**; default is pure-markdown OKF (no binaries in bundle).
- `log.md` is fed from the **real event log** (timeline subsystem), not invented data.
- Event store: **one tagged `events` table** (`category` = audit | content) + `details` JSON;
  one `TimelineService` read path. Events carry **what + why** (audit: placement rationale)
  and a separate **content/timeline** stream (past + future + recurring).
- Recurrence: store the **rule once**, expand **on read** within a bounded, configurable
  horizon (no materialised rows).
- Faithful round-trip needs machine-readable extras (`saga-manifest.json` +
  `saga-events.jsonl`) that OKF consumers ignore; `document_id`/`event_id` are preserved.
- Requirement IDs assigned: **FR-44…FR-56** (timeline + OKF) and **NFR-36** (OKF determinism /
  conformance) in `docs/requirements/`.

---

## 2. Done & merged

| Capability | Where | Refs |
|---|---|---|
| Timeline Phase 1 — audit event store + `TimelineService` + `GET /timeline` + MCP `get_timeline` | saga-core | PR #2 · FR-44/45/48 |
| Timeline Phase 2 — content/timeline extraction (`extract_timeline`, past/future/recurring) | saga-core | PR #4 · FR-46/50 |
| Timeline Phase 3 — on-read recurrence expansion + `GET /agenda` + MCP `get_agenda` | saga-core | PR #7 · FR-47/49 |
| OKF export v1 — bundle, concept files, `index.md`/`log.md`, `--with-originals`, client | saga-core | PR #3 · FR-51/52 |
| OKF export manifest — `saga-manifest.json` + `saga-events.jsonl` | saga-core | PR #5 · FR-53 |
| OKF import — faithful round-trip + foreign re-enrich + `index_document` job + client | saga-core | PR #6 · FR-54/55/56 |
| Frontmatter / format contract (Track D) — `saga_*` keys, `type` fallback, `resource` URI | saga-core | folded into PR #3/#5/#6 · FR-51/53 |
| saidex 0.3 migration (`extract_data_*` API), `<0.3` pin lifted | saga-core | PR #8 |
| Requirement IDs for timeline + OKF | docs/requirements | FR-44…56, NFR-36 |
| **Track E — saga-ui**: Timeline view, document-detail timeline, agenda/"upcoming", folder view (via the BFF generic proxy) | saga-ui | PR #2 · UI |
| **Track H — saga-backup**: `events` confirmed captured by the whole-DB `pg_dump`; regression test pins it; legacy `export` kept (complementary, documented) | saga-backup | PR #2 |
| **Track I (local)**: in-repo OKF v0.1 conformance check over a full bundle (`tests/export/test_okf_conformance.py`) | saga-core | NFR-36 |
| Timeline emission test debt — stage-level "no event on unchanged re-ingest" guards | saga-core | `tests/test_pipeline_stages.py` |

The **core technical arc is complete**: the timeline subsystem and the full OKF export↔import
round-trip are implemented, tested, merged to `develop`, **and surfaced in the UI**.

---

## 3. Remaining work

### Track E — saga-ui (frontend + BFF)  — ✅ **done (saga-ui PR #2)**

- [x] **Timeline view** consuming `GET /timeline` (audit ↔ content toggle = `category`).
- [x] **Document detail** timeline (`GET /documents/{id}/timeline`).
- [x] **Agenda / "upcoming" view** consuming `GET /agenda`.
- [x] **Folder view**: `index.md`-style overview + per-folder log.
- [x] BFF: the **generic `/api/{path}` proxy** forwards the new saga-core endpoints with no
      BFF change; auth is the existing session gate. (Export/import UI not surfaced — see below.)
- [ ] **Export / import from the UI** — deferred. The BFF can proxy `GET /export/okf` /
      `POST /import/okf`; decide later whether to add a UI affordance.

### Track H — saga-backup / restore interplay — ✅ **done (saga-backup PR #2)**

- [x] **Verified** the `events` table is captured by the whole-DB `pg_dump` and restored by
      `pg_restore`; a regression test (`tests/test_archive.py`) pins the dump as whole-DB so a
      later table filter can't silently drop it.
- [x] **Decision: keep** saga-backup's `export` command. It is *complementary* to OKF export —
      the binary `archive`/`restore` + folder `export` remain the canonical backup path (direct
      DB/MinIO, no re-analysis on restore); OKF export is the interchange projection. Documented
      in `saga-backup/README.md`.
- [x] saga-backup `export.py` SQL confirmed in sync with the current schema; `events` is
      additive and does not touch its queries.

### Track I — interop / conformance / ecosystem

- [x] **Local OKF v0.1 conformance** (NFR-36): in-repo check over a full bundle — concept
      frontmatter (`type` required, `title`, `tags` list), reserved `index.md`/`log.md` carry no
      frontmatter, and the only non-markdown files are the ignorable machine extras
      (`tests/export/test_okf_conformance.py`).
- [ ] **External** consumption check — Google's static HTML graph visualizer and (aspirationally)
      the BigQuery Knowledge Catalog ingestion — on the test server (10.0.0.220). *Needs the
      running stack + a human to drive the external tools; flagged for the owner.*
- [ ] License/attribution for emitting OKF; track upstream spec changes (v0.1 is young).

### Deferred / smaller items (acceptable as-is for now)

- [x] **Lossless foreign round-trip — shipped.** Added a free-form `Document.metadata`
      (`dict[str, str]`): non-reserved frontmatter keys are captured on import and re-emitted
      top-level on export, so foreign bundles round-trip losslessly. Editable via REST `PATCH` +
      the MCP write tool, and projected to OpenSearch (full-text + filterable). Spec:
      `docs/superpowers/specs/2026-06-18-document-metadata-design.md` (FR-57). *(saga-ui editor
      is a separate follow-on deliverable.)*
- [ ] **Streaming importer** for very large bundles (v1 extracts to a temp dir / reads into
      memory).
- [x] **Timeline test debt — resolved.** Stage-level guards now have tests: `classify_doc_type`
      emits a reclassification only when the doc-type changes, and `place_in_folder` emits a
      placement only when the folder set changes (idempotent re-ingest stays silent). **`actor`
      stays a free-form string** (audit `pipeline`/`system`, content `llm`; no value in an enum
      that an importer would have to round-trip). **`doc_ingested` stays where it is** — it
      records the *attempt*; ingestion failures surface via document status, not by withholding
      the event.
- [ ] **Docs:** update saga-core docs and the workspace root README to describe the OKF
      capability + a user-facing how-to. (`saga-backup/README` done in PR #2.)

### Optional — saidex upstream (separate repo)

- [ ] Add the reusable RRULE validator (round-trip design Appendix A) to **saidex**; then swap
      SAGA's local `src/saga/llm/rrule.py:validate_rrule` for `from saidex.validators import
      validate_rrule` (one-line change). saidex 0.3 already ships a `Validator` hook + built-in
      validators, so this fits naturally.

### Track G — saga-importer (.NET tray)

- [ ] Out of scope for now. Possible later: watch a folder of OKF bundles and import them via
      `POST /import/okf`. Windows-only, REST client, no Docker.

---

## 4. Recommended next sequencing

Tracks E, H, and the local half of I are now done. What remains:

1. **`Document.metadata` bag** — the one functional gap (lossless foreign round-trip). Needs a
   brainstorm (schema migration + import/export wiring) before implementation.
2. **Docs sweep** — saga-core docs + workspace root README describe the OKF capability + how-to.
3. **Track I (external)** — OKF v0.1 consumption by Google's visualizer / BigQuery on the test
   server; owner-driven (needs the running stack + the external tools).
4. Deferred items (streaming importer) + the **saidex** upstream RRULE validator as they
   become relevant. *(saidex is not yet released — held.)*
