# SAGA ↔ OKF Integration — Open Items & Roadmap

> **Living tracker.** Captures everything still to be implemented, discussed, or kept in
> mind to bring SAGA and the **Open Knowledge Format (OKF)** together — across *all* parts
> of the ecosystem (saga-core, saga-backup, saga-ui, saga-importer). Check items off as they
> land; add new ones as they surface. This is a planning doc, not a spec — each Track that
> says "own spec" goes through the normal brainstorm → spec → plan → implement cycle.

- **Created:** 2026-06-17
- **Related docs:** [timeline event-log design](superpowers/specs/2026-06-16-timeline-event-log-design.md) ·
  [timeline Phase-1 plan](superpowers/plans/2026-06-16-timeline-event-log-phase1.md)
- **Scope note:** OKF is an **interchange/serialization format at the boundary**, never the
  database storage model. Postgres stays the system of record; OpenSearch stays a rebuildable
  projection; an OKF bundle is a *third* projection (export) and an *import* source.

---

## 1. Decisions already made (do not re-litigate)

- OKF = interchange/serialization layer (export + import), **not** a DB schema change.
- The existing `saga-backup export` command will be **replaced** by OKF-bundle output (the
  old binary + `.md` + `.metadata.json` layout goes away).
- Originals: **`--with-originals` flag**; default is pure-markdown OKF (no binaries in bundle).
- `log.md` must be fed from a **real event log**, not invented data → the timeline/event-log
  subsystem was built first as the prerequisite.
- Event store architecture: **one tagged `events` table** with a `category` discriminator
  (`audit` | `content`) + `details` JSONB; one `TimelineService` read path.
- Events carry **what + why** (audit: placement rationale = similar docs + folder votes) and a
  separate **content/timeline stream** (dates/events/appointments from document text), each
  tagged so consumers can choose the view.
- Content events cover **past + future + recurring**.
- One design doc for the timeline subsystem; **phased** implementation.

---

## 2. Current status

- ✅ **Timeline Phase 1** (audit event log: `events` table + migration `0004`, `Event` model,
  `append_event`/`query_events`, `EventRecorder`, `TimelineService`, `GET /timeline` +
  `GET /documents/{id}/timeline`, MCP `get_timeline`) — implemented, tested (402 passed, 85%),
  committed on `feature/timeline-event-log` (commits incl. follow-ups `…/7377df6`). **Not yet
  merged/pushed.**
- ✅ Review follow-ups applied: membership-resolved folder scoping (§6.2), emit-on-change
  instead of content-hash dedupe (reverts recorded), `folder_renamed`, migration
  `server_default`, MCP invalid-category error, pagination tiebreaker, comment cleanup.
- ⏳ Spec + Phase-1 plan + this roadmap are **untracked** in `saga-core/docs/` (commit when ready).
- ❌ OKF export, OKF import, frontmatter contract, UI/agent surfacing — **not started**.

---

## 3. Track 0 — Housekeeping / prerequisites

- [ ] Merge `feature/timeline-event-log` → push, open PR against `develop`, squash-merge.
- [ ] Commit the 5 `docs:` CLAUDE.md changes' branches (saga-ui/backup/importer/ollama_guard
      are on their own branches; saga-importer on `docs/drop-coauthor-trailer`) — decide
      whether to relocate the off-topic ones to dedicated branches.
- [ ] Commit `docs/superpowers/` spec + plan + this roadmap (currently untracked).
- [ ] **Assign real requirement IDs** in `docs/requirements/` for the timeline + OKF features
      (placeholder `FR-TL-1`/`FR-timeline-mcp` were removed; nothing real exists yet).
- [ ] Decide the **canonical home** for this roadmap (saga-core/docs vs workspace root, since
      it spans repos).

---

## 4. Track A — Finish the timeline subsystem (feeds `log.md`)

Spec already written; each phase needs its own implementation plan.

### Phase 2 — content/timeline stream (LLM)
- [ ] New `extract_timeline` analyzer step (saidex JSON mode), prompt in
      `prompts/analysis/timeline-extraction.md`, wired into the `analyzing` stage after
      `extract_values`.
- [ ] `TimelineEvent` saidex schema (`kind` past/future/recurring, `description`, `date`,
      `recurrence`, `source_quote`, `confidence`).
- [ ] Persist as `category=content` events; **delete+reinsert** content events on re-analysis
      (derivable projection).
- [ ] Content-event folder scope resolved via current membership at read time (already the
      query behaviour after the C1 follow-up — confirm it covers content rows too).
- **Open questions:**
  - [ ] Date-normalization rules (resolve relative dates against a reference date in the doc;
        drop dateless hits).
  - [ ] Confidence threshold for keeping an event?
  - [ ] How aggressively to extract (precision vs recall) — prompt tuning + eval set.

### Phase 3 — recurrence + consumers
- [ ] RRULE storage + **on-read expansion** over a bounded horizon.
- [ ] Add a **recurrence-horizon** config knob (current `TimelineConfig` only has
      `rationale_top_n` / page sizes).
- [ ] "Upcoming / agenda" query path (future + expanded recurring within horizon).
- **Open questions:**
  - [ ] RRULE library (dateutil-style dependency) vs a constrained in-house subset?
  - [ ] Reminders/notifications subsystem — explicitly deferred; schema must not preclude it.

### Phase-1 test debt (deferred, acceptable for now)
- [ ] Add stage-level tests for "same type → no emit" and placement no-op on re-ingest.
- [ ] Note: `doc_ingested` is emitted before ingestion success — timeline shows ingests that
      may later fail. Decide if acceptable or move the event to success.
- [ ] `actor` is a free-form string (not an enum) — decide whether to constrain.

---

## 5. Track B — OKF **export** (the core "bring together" deliverable)  — own spec

Home: `saga-backup` (replaces its `export` command). **Note:** saga-backup talks **directly to
Postgres/MinIO** (not the REST API), so it cannot reuse `saga-core`'s `TimelineService`.

- [ ] **Frontmatter contract** (see Track D) — the format spec both export and import share.
- [ ] Concept file per document: YAML frontmatter folded in + body = extracted markdown.
- [ ] `--with-originals` flag (default pure markdown; flag colocates the binary).
- [ ] Per-folder **`index.md`** (folder note): OKF section + bullet list of children/docs with
      descriptions; root `index.md` too.
- [ ] Per-folder **`log.md`**: date-grouped, newest-first, **category-tagged** (audit +
      content) entries.
- **Open questions / design points:**
  - [ ] **`log.md` data access from saga-backup:** duplicate the membership-resolved event SQL
        in saga-backup, OR extract a shared query helper, OR (against current design) expose a
        read endpoint. Pick one.
  - [ ] **`resource` URI scheme:** `saga://<store>/documents/<id>`? the MinIO object path? a
        configurable base URL to the UI/API? Define and make it round-trippable.
  - [ ] **`type` fallback** when `doc_type` is null (OKF requires non-empty `type`) — e.g.
        `document`.
  - [ ] **Concept IDs / filenames:** keep `<title>__<doc_id>.md`? OKF derives the concept id
        from the path — decide a stable, collision-free, git-diffable naming + folder-path
        sanitization (spaces, unicode, case).
  - [ ] **Cross-links:** should the export emit OKF markdown links between concepts (e.g. to
        similar docs, or doc→folder index)? OKF links assert relationships.
  - [ ] **Extension-key naming** for SAGA-specific metadata (`saga_id`, `saga_doc_type_id`,
        `saga_extracted_values`, `saga_content_hash`, `saga_status`, `saga_folders`,
        `saga_notes`, …) — namespacing convention.
  - [ ] **Integrity/manifest:** OKF doesn't require it; do we add a SHA-256 manifest like the
        `archive` command for backup fidelity?
  - [ ] **Determinism** (stable ordering + filenames) so bundles are git-diffable.
  - [ ] **Performance**: stream large archives; memory bounds.
  - [ ] **Relationship to `archive`/`restore`:** OKF export *complements*, does not replace, the
        binary backup. Keep both. (The `events` table is now in the pg_dump → restored
        automatically; verify.)
  - [ ] saga-backup schema-sync warning: its README/CLAUDE.md notes that new migrations may
        require updating `export.py` SQL — the new `events` table is now a dependency.

---

## 6. Track C — OKF **import** — own spec

Home: likely `saga-core` ingestion (a new bundle source), or a CLI/API entry point.

- [ ] Read a bundle dir: parse YAML frontmatter + markdown body per concept file.
- [ ] Map frontmatter → `Document` fields; `type` → doc-type (create if missing).
- [ ] Body → `content_markdown`; re-chunk/embed/index (OpenSearch projection).
- [ ] Folder reconstruction from bundle path hierarchy vs re-running LLM placement.
- **Open questions / design points:**
  - [ ] **Trust policy:** trust provided frontmatter metadata vs re-enrich via the LLM (or use
        as a prior).
  - [ ] **Binary handling:** if `resource`/colocated original exists, ingest it; else store the
        markdown body as the document.
  - [ ] **Dedup** on import via `content_hash` (skip/update existing).
  - [ ] **Unknown frontmatter keys:** ⚠️ `Document` has **no free-form metadata bag** (only
        `extracted_values` + `notes`; `Folder` has `metadata`). Decide: add a metadata dict to
        `Document` for lossless round-trip, or accept lossy import.
  - [ ] `index.md` / `log.md` on import: ignore (consumer-synthesizable) for v1, or use them to
        seed folder notes / history?
  - [ ] Importing a foreign (non-SAGA) OKF bundle — be permissive (OKF tolerates missing
        fields/broken links/unknown types).

---

## 7. Track D — Frontmatter / format contract (underpins B + C)

- [ ] Define the SAGA↔OKF field mapping once (could be a small standalone spec consumed by both
      export and import):

  | OKF field | SAGA source | Notes |
  |---|---|---|
  | `type` *(required)* | `Document.doc_type` | fallback `document` when null |
  | `title` | `Document.title` | |
  | `description` | `Document.summary` | |
  | `resource` | original (MinIO) | URI scheme TBD |
  | `tags` | folder names / value terms | TBD |
  | `timestamp` | `updated_at` | ISO 8601 |
  | extension keys | `document_id`, `content_hash`, `extracted_values`, `mime_type`, `folders`, `notes`, `status` | namespacing TBD |

- [ ] Decide OKF spec **version** we target (v0.1 today; young + evolving — pin it).
- [ ] Round-trip fidelity statement: what survives export→import losslessly.

---

## 8. Track E — saga-ui (frontend + BFF)

- [ ] **Timeline view** consuming `GET /timeline` (audit vs content toggle = `category`).
- [ ] **Document detail**: show that document's timeline (`/documents/{id}/timeline`).
- [ ] **Folder view**: `index.md`-style overview + per-folder log.
- [ ] **Upcoming/agenda** view from content/recurring events (after Track A Phase 2/3).
- **Open questions:**
  - [ ] Trigger **export/import from the UI?** The BFF proxies to saga-core's REST API, but the
        OKF export lives in saga-backup (no REST). Either add a saga-core export endpoint, or
        keep export CLI-only. Decide.
  - [ ] BFF auth/permissions for timeline + any OKF endpoints.

---

## 9. Track F — MCP / agent surface

- [x] `get_timeline` tool (Phase 1).
- [ ] Agent-facing "what's coming up / what happened to doc X" once content events exist.
- [ ] Decide whether export/import are exposed as MCP tools (probably not).

---

## 10. Track G — saga-importer (.NET tray)

- [ ] Out of scope for now, but note: could it watch a folder of OKF bundles and import them
      (vs single files)? Decide later. Windows-only, no Docker, REST client only.

---

## 11. Track H — backup / restore interplay

- [ ] OKF export is **additive** to `archive`/`restore` (binary backup); keep both.
- [ ] Confirm the `events` table is included in the pg_dump archive and survives `restore`
      (whole-DB dump → yes; add a verify check).
- [ ] NAS pull scripts (rsync/robocopy) + GFS retention unaffected — confirm.

---

## 12. Track I — Interop, conformance, ecosystem

- [ ] Validate our bundles **conform to OKF v0.1** (parseable frontmatter, non-empty `type`,
      reserved-file roles for `index.md`/`log.md`).
- [ ] Test consumption by **Google's static HTML graph visualizer** and (aspirationally) the
      **BigQuery Knowledge Catalog** ingestion.
- [ ] License/attribution for emitting OKF; track upstream spec changes.

---

## 13. Cross-cutting

- [ ] **Config:** export output dir, OKF options, `resource` base URL, recurrence horizon,
      content-extraction toggles — all config-driven (no constants).
- [ ] **Security:** timeline routes are auth-gated (done). OKF bundles may contain sensitive
      content — define where exports land and access control.
- [ ] **Testing:** per-project unit tests + a cross-stack round-trip (export → import →
      compare) integration test on the test server (10.0.0.220).
- [ ] **Docs:** update `saga-backup/README`, saga-core docs, and the workspace root README to
      describe the OKF capability; user-facing how-to.
- [ ] **Build check:** mandatory `docker compose build api worker` (+ `saga-ui`) after changes.

---

## 14. Recommended sequencing

1. **Track D (frontmatter contract)** — small, unblocks B and C. *(could fold into B's spec)*
2. **Track B (OKF export)** — highest visible value; ships bundles with frontmatter +
   `index.md` + **audit-only** `log.md` on top of Phase 1.
3. **Track A Phase 2** — enriches `log.md` with the content/timeline stream.
4. **Track C (OKF import)** — closes the round-trip.
5. **Track A Phase 3**, **Track E (UI)**, **Track I (interop validation)** — in parallel/after.

> The immediate gating decision for `log.md` in Track B is **§5's "log.md data access from
> saga-backup"** — resolve that before/within the export spec.
