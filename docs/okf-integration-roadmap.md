# SAGA ↔ OKF Integration — Open Items & Roadmap

> **Living tracker.** Captures what has landed and what is still to do to bring SAGA and the
> **Open Knowledge Format (OKF)** together across the ecosystem (saga-core, saga-backup,
> saga-ui, saga-importer). This is a planning doc, not a spec — each remaining Track that says
> "own spec" goes through the normal brainstorm → spec → plan → implement cycle.

- **Created:** 2026-06-17 · **Last updated:** 2026-06-17 (post merge of PR #8)
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

The **core technical arc is complete**: the timeline subsystem and the full OKF export↔import
round-trip are implemented, tested, and merged to `develop`.

---

## 3. Remaining work

### Track E — saga-ui (frontend + BFF)  — *not started; biggest remaining piece*

Make the backend capability user-visible. Needs its own brainstorm → spec → plan.

- [ ] **Timeline view** consuming `GET /timeline` (audit ↔ content toggle = `category`).
- [ ] **Document detail** timeline (`GET /documents/{id}/timeline`).
- [ ] **Agenda / "upcoming" view** consuming `GET /agenda`.
- [ ] **Folder view**: `index.md`-style overview + per-folder log.
- [ ] **Export / import from the UI?** The BFF can now proxy `GET /export/okf` and
      `POST /import/okf` (both are saga-core REST). Decide whether to surface them.
- [ ] BFF auth/permissions for the new surfaces.

### Track H — saga-backup / restore interplay

- [ ] **Verify** the `events` table is included in the `pg_dump` archive and survives
      `restore` (whole-DB dump → expected yes; add an explicit verify check).
- [ ] **Decide** the fate of saga-backup's legacy `export` command now that OKF export lives
      in saga-core (keep, deprecate, or remove). The binary `archive`/`restore` path stays.
- [ ] saga-backup `export.py` SQL: confirm it doesn't break against the newer schema (the
      `events` table is a new dependency surface).

### Track I — interop / conformance / ecosystem

- [ ] Validate exported bundles **conform to OKF v0.1** (NFR-36) with an external/automated
      check, not just our own tests.
- [ ] Test consumption by Google's static HTML graph visualizer and (aspirationally) the
      BigQuery Knowledge Catalog ingestion — ideally on the test server (10.0.0.220).
- [ ] License/attribution for emitting OKF; track upstream spec changes (v0.1 is young).

### Deferred / smaller items (acceptable as-is for now)

- [ ] **Lossless foreign round-trip:** `Document` has no free-form metadata bag, so unknown
      (non-`saga_*`, non-standard) frontmatter keys are dropped on foreign import. A
      `Document.metadata` dict would close this (round-trip spec §11).
- [ ] **Streaming importer** for very large bundles (v1 extracts to a temp dir / reads into
      memory).
- [ ] **Timeline test debt:** stage-level "same doc-type → no reclassification event" test;
      decide whether `doc_ingested` (emitted before ingestion success) should move to success;
      decide whether `actor` should be a constrained enum vs free-form string.
- [ ] **Docs:** update saga-core docs, `saga-backup/README`, and the workspace root README to
      describe the OKF capability + a user-facing how-to.

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

1. **Track E (saga-ui)** — highest user-visible value; surfaces timeline, agenda, and folder
   views (and optionally export/import) that the backend already supports.
2. **Track H** — quick verify (events in `pg_dump`) + the saga-backup `export` retire/keep
   decision.
3. **Track I** — external OKF conformance validation once a bundle is demoed end-to-end.
4. Deferred items + the saidex upstream as they become relevant.
