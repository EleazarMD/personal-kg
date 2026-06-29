# PCG Agentification & Email Personalization — Plan

**Status**: Living doc. Created 2026-04-21 after mapping actual schema state.
**Engine**: Hatchet (`services/workflows/`) orchestrates. PCG is the durable
personal-context substrate. CIG owns the social/comms graph.

---

## Terminology

- **PCG** (Personal Context Graph) — the service. Current name.
- **PIC** — legacy subsystem name (Personal Identity Core) folded into PCG v2.
  Survives only in: `/api/pic/*` URL prefix, module names (`pic_routes.py`,
  `pic_auth.py`), env vars (`PIC_READ_KEY` / `PIC_ADMIN_KEY`), and Neo4j
  constraint names (`pic_*`). Semantically it is PCG.
- **CIG** (Communications Intelligence Graph) — separate service on port 8780.
  Owns all email/calendar/contact data.

**Decision for new routes (2026-04-21)**: Mount new endpoints under `/api/pcg/*`
(primary) AND keep `/api/pic/*` active as deprecated aliases for existing
callers (Nova, Hermes, OpenClaw). Do NOT rename existing routes in this work.

## Architectural reality (discovered 2026-04-21)

PCG and CIG **share one Neo4j** (`hermes-neo4j` container, bolt://localhost:7689,
Neo4j 5). They have been developed as separate subgraphs with **no connecting
edges** — this work adds those edges.

### Topology

```
               hermes-neo4j (bolt://localhost:7689, Neo4j 5, SHARED)
 ┌───────────────────────────────────────────────────────────────────┐
 │ CIG subgraph (world-side, OWNED by cig service port 8780)         │
 │   :Email, :Person, :Thread, :Topic, :CalendarEvent, :Briefing,    │
 │   :ActionItem, :Contact, :Interaction, :Account, :Task,           │
 │   :OutboundEmail, :Tag, :BlockedSender, :BlockedDomain            │
 │   Edges: SENT, SENT_TO, REPLIES_TO, HAS_TOPIC, HAS_ATTENDEE,      │
 │          COMMUNICATES_WITH, HAD_INTERACTION, HAS_ACTION, ...      │
 │                              ▲                                    │
 │                              │ cross-subgraph edges (PCG writes,  │
 │                              │ PCG→CIG direction only; never      │
 │                              │ mutates CIG node properties)       │
 │                              │                                    │
 │ PCG subgraph (user-side, OWNED by pcg service port 8765)          │
 │   :Identity, :Preference, :Goal, :Observation, :Entity,           │
 │   :CommunicationStyle (NEW), :WorkflowRun (NEW), :Insight (NEW)   │
 │   Cross-edges: APPLIES_TO, DERIVED_FROM, STYLE_FOR, OWNS,         │
 │                AUDITED_BY, PROMOTED_TO, CONSOLIDATES              │
 └───────────────────────────────────────────────────────────────────┘
                                ▲
                                │ gRPC :7077
                                │
     ┌──────────────── Hatchet engine (port 8768) ────────────────┐
     │                                                             │
     ▼                            ▼                                ▼
  pi-agent-hub TS worker    cig-workflows Python worker    pcg-workflows (NEW, C1)
  (hermes-comms-sweep,      (cig.fetch-recent-emails,      (pcg.daily-
   email-respond-with-       cig.send-email)                consolidation)
   approval — NEW B2)
```

### Ownership rules (hard invariants)

| Rule | Enforced how |
|---|---|
| CIG-owned labels (Email, Person, Thread, Topic, CalendarEvent, Briefing, ActionItem, Contact, Interaction, Account, Task, OutboundEmail, Tag, BlockedSender, BlockedDomain) MUST only be MATCHed by PCG code — never MERGE/SET/DELETE | Code review; PCG's `graph_store.py` additions use `MATCH` only for CIG labels |
| PCG-owned labels (Identity, Preference, Goal, Observation, Entity, Document, Community, CommunicationStyle, WorkflowRun, Insight) MUST only be written by PCG | Symmetric; CIG workers never touch them |
| Cross-subgraph edges are PCG-owned. Direction always PCG → CIG | Written in PCG code; audited via edge property `owner='pcg'` |
| Migrations additive-only; no label renames or property deletions | `migrations.py` uses `IF NOT EXISTS` + `coalesce()` backfills |

### Why one Neo4j, not two

A single traversal can join user self-model to email history in one Cypher.
Example: `GET /api/pcg/contact-context?email=<addr>` (Phase A5) joins
`:Identity → :Preference → :APPLIES_TO → :Person ← :SENT_TO ← :Email` in a
single query — no cross-HTTP joins, no eventual consistency. This is the
entire reason email personalization via PCG is worth building.

### Live counts (2026-04-21, from shared Neo4j)

CIG-owned: `:Email` 108,544 · `:ActionItem` 374,448 · `:Person` 20,243 ·
`:Topic` 1,355 · `:Thread` 531 · `:CalendarEvent` 172 · `:Briefing` 111 ·
`:Contact` 1 · `:Interaction` 1

PCG-owned: `:Entity` 1,366 · `:Observation` 30 · `:Preference` 4 ·
`:Identity` 1 · `:Goal` 0

### The gap
- No edges between PCG user-side subgraph and CIG's social graph.
- No `:CommunicationStyle` (per-contact tone/voice).
- No `:WorkflowRun` (Hatchet audit).
- No vector indexes on any node (embeddings live in Chroma, decoupled).
- ChromaDB uses default sentence-transformer — not AI-Gateway-routed.
- `Preference` has no scope: can't have "short emails to mom, formal to clients".
- `Observation` is flat text — no structured category/key/value, no confidence,
  no decay, no promotion path.

---

## Design principles

1. **Don't duplicate CIG.** Contact/Thread/Topic are CIG's. PCG links into them.
2. **PIC is the user's self-model.** Preferences, goals, voice, observations.
3. **One Neo4j, many subgraphs.** Clearly-labeled ownership, shared traversal.
4. **Every workflow audits to PCG.** `:WorkflowRun` node regardless of outcome.
5. **Observations decay, preferences persist.** Consolidation agent promotes
   repeated observations into preferences; unused observations decay.
6. **Embeddings via AI Gateway.** Central provider routing, cost tracking.
7. **Neo4j 5 vector indexes.** Unify graph + vector; retire Chroma dual-store
   for PIC nodes (keep Chroma only for document chunks).

---

## Target schema additions (PCG owns)

### New node labels
| Label | Purpose | Key | Vector field |
|---|---|---|---|
| `:CommunicationStyle` | Per-context tone/voice | `id` | `embedding` |
| `:WorkflowRun` | Hatchet audit | `run_id` | — |
| `:Insight` | Consolidation-agent output | `id` | `embedding` |

### Upgraded existing labels (PCG)
- `:Preference` gains: `scope` (`global` \| `contact:<email>` \| `topic:<name>`),
  `confidence` (0-1), `source` (`explicit` \| `observed` \| `consolidated`),
  `last_reinforced_at`, `embedding`.
- `:Observation` gains: structured `category`/`key`/`value`/`context` fields
  (already used by Nova client but not persisted that way server-side),
  `confidence`, `status` (`new` \| `promoted` \| `decayed`), `embedding`.

### New edges (cross-subgraph)
| Edge | From → To | Meaning |
|---|---|---|
| `:APPLIES_TO` | `Preference` → `Person`\|`Topic`\|`Thread` | Scoped preference |
| `:DERIVED_FROM` | `Observation` → `Email`\|`Interaction` | Grounded learning |
| `:STYLE_FOR` | `CommunicationStyle` → `Person`\|`Topic` | Per-contact voice |
| `:OWNS` | `Identity` → `CommunicationStyle` | User's voice library |
| `:AUDITED_BY` | any → `WorkflowRun` | Hatchet audit trail |
| `:PROMOTED_TO` | `Observation` → `Preference` | Consolidation provenance |
| `:CONSOLIDATES` | `Insight` → `Observation` | Daily-roll-up provenance |

### Constraints to add
```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (c:CommunicationStyle) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (w:WorkflowRun) REQUIRE w.run_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (i:Insight) REQUIRE i.id IS UNIQUE;
CREATE INDEX pref_scope IF NOT EXISTS FOR (p:Preference) ON (p.scope);
CREATE INDEX obs_status_created IF NOT EXISTS FOR (o:Observation) ON (o.status, o.created_at);
CREATE INDEX workflow_run_workflow IF NOT EXISTS FOR (w:WorkflowRun) ON (w.workflow_name, w.started_at);
```

### Vector indexes (Neo4j 5 native)
```cypher
CREATE VECTOR INDEX preference_embedding IF NOT EXISTS
  FOR (p:Preference) ON (p.embedding)
  OPTIONS { indexConfig: { `vector.dimensions`: 768, `vector.similarity_function`: 'cosine' }};
CREATE VECTOR INDEX observation_embedding IF NOT EXISTS
  FOR (o:Observation) ON (o.embedding)
  OPTIONS { indexConfig: { `vector.dimensions`: 768, `vector.similarity_function`: 'cosine' }};
CREATE VECTOR INDEX communication_style_embedding IF NOT EXISTS
  FOR (c:CommunicationStyle) ON (c.embedding)
  OPTIONS { indexConfig: { `vector.dimensions`: 768, `vector.similarity_function`: 'cosine' }};
CREATE VECTOR INDEX insight_embedding IF NOT EXISTS
  FOR (i:Insight) ON (i.embedding)
  OPTIONS { indexConfig: { `vector.dimensions`: 768, `vector.similarity_function`: 'cosine' }};
```
Dimensions = 768 to match `nomic-embed-text` (the model the `.env` currently
declares). Will be parameterized via config.

---

## Phase A — Schema hardening (foundation)

- [x] A0: Discover shared-Neo4j reality, catalog existing labels/edges/indexes
- [ ] **A1**: Draft migration module `services/personal-kg/services/personal-kg/migrations.py`
      that applies constraints + vector indexes idempotently on startup
- [ ] **A2**: Extend `models.py` with `CommunicationStyle`, `WorkflowRun`,
      `Insight`, upgraded `Preference`/`Observation` fields
- [ ] **A3**: Extend `graph_store.py` with CRUD for new labels and cross-subgraph edges
- [ ] **A4**: New `embeddings.py` → AI-Gateway-backed embedder; fallback to
      local if gateway unreachable. Used by every new write to populate `embedding`.
- [ ] **A5**: Route additions under `/api/pcg/*` (new primary namespace).
      Also mount the same handlers at `/api/pic/*` as deprecated aliases so
      existing Nova/Hermes/OpenClaw callers keep working:
      - `POST /api/pcg/communication-style`
      - `GET  /api/pcg/communication-style?contact=<email>`
      - `POST /api/pcg/workflow-run` (called by Hatchet activities)
      - `GET  /api/pcg/contact-context?email=<email>` — **the key endpoint
        for email personalization**; returns identity + scoped prefs +
        communication-style + recent interactions joined from CIG's graph
      - `GET  /api/pcg/insights/recent?limit=10` (used by Phase C2)
- [ ] **A6**: Update Nova `pcg.py` client with new methods

### A6 migration safety
- All additions are **additive**. No label renames. No edge deletions.
- Existing `:Preference` nodes get `scope='global'` / `source='explicit'` / `confidence=1.0` backfill.
- Existing `:Observation` nodes: leave text `observation` field; new
  structured fields are nullable.

---

## Phase B — Hatchet workflows use PCG for email personalization

- [ ] **B1**: Add `pcg.queryContactContext(email)` and `pcg.recordWorkflowRun(...)`
      to `services/pi-agent-hub/src/workflows/services.ts` (`HubServices`)
- [ ] **B2**: New workflow `email-respond-with-approval` in
      `services/pi-agent-hub/src/workflows/email-respond.ts`:
      ```
      fetch-email → query-pcg-context → hermes-draft (with personalization prompt)
        → request-approval → (approved?) persist-interaction → record-workflow-run
      ```
- [ ] **B3**: Enrich `hermes-comms-sweep` step `resolve-context` to include
      PCG contact context for top-N urgent threads
- [ ] **B4**: Every workflow ends by calling `pcg.recordWorkflowRun` (pulled
      forward from Phase 5 of `services/workflows/PLAN.md`). Tasks that wrote
      observations/interactions link them via `:AUDITED_BY`

---

## Phase C — Agentify PCG (daily consolidation)

- [ ] **C1**: New Python Hatchet worker at
      `services/personal-kg/services/personal-kg/workflows/worker.py`.
      Workflow `pcg.daily-consolidation`:
      ```
      gather-recent-observations (last 24h, status='new')
        → cluster (embedding similarity + LLM label)
        → for-each-cluster: { summarize → create Insight → link :CONSOLIDATES }
        → promote (clusters with size≥3 and mean confidence≥0.7)
            → upsert Preference/CommunicationStyle + :PROMOTED_TO edge
        → decay (observations older than 30d with status='new' → 'decayed')
        → record-workflow-run
      ```
      Cron: `0 6 * * *` (daily 6am UTC)
- [ ] **C2**: Endpoints:
      - `POST /api/pic/consolidate` — manual trigger (queues workflow run)
      - `GET /api/pic/insights/daily?date=YYYY-MM-DD` — read insights
      - `GET /api/pic/insights/recent?limit=10` — dashboard/Nova
- [ ] **C3**: Nova skill `pcg_insights` — "What did you learn about me yesterday?"

---

## Integration with `services/workflows/PLAN.md`

This plan executes Phase 4 + Phase 5 items already scoped there:
- Phase 4 `email-respond-with-approval` → our **B2**
- Phase 4 `scheduled-memory-consolidation` → our **C1**
- Phase 5 `:WorkflowRun` auto-write → our **B4**

No new decisions needed from the workflow plan. We're executing its backlog
with the schema now properly designed against the real Neo4j state.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Writes to shared Neo4j break CIG | Additive-only migrations; strict label ownership; CIG owns `Email`/`Person`/`Thread`/`Topic`/`Contact`/`Interaction`, PCG writes its own labels + edges TO those but never mutates them |
| `nomic-embed-text` not actually available via AI Gateway | Fallback to local sentence-transformer; probe gateway at startup |
| Vector index dim mismatch on backfill | Gate backfill behind explicit admin trigger; log + skip rows with wrong dim |
| Consolidation agent promotes wrong patterns | `confidence` threshold + human-readable insight written first; promotion gated by `min_cluster_size=3` + `mean_confidence>=0.7`; always reversible via `:PROMOTED_TO` edge provenance |
| Hatchet worker in PCG conflicts with CIG worker | Separate worker process, own service name, own queue; shares only Neo4j client |

---

## Change log

- 2026-04-21 — Plan created after schema discovery. CIG shares `hermes-neo4j`
  with PCG; design revised to link rather than duplicate.
