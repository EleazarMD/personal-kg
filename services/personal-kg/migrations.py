"""
PCG schema migrations — idempotent. Runs on API startup.

All migrations are ADDITIVE. This Neo4j instance (`hermes-neo4j`) is shared
with CIG which owns the email/contact/calendar subgraph (Email, Person,
Thread, Topic, CalendarEvent, Contact, Interaction). PCG owns the user-side
subgraph (Identity, Preference, Goal, Observation, CommunicationStyle,
WorkflowRun, Insight, Entity) and may CREATE edges pointing at CIG's nodes
but must never mutate their properties or labels.

See services/personal-kg/AGENTIFICATION_PLAN.md for the full design.
"""

from __future__ import annotations

from typing import List
from neo4j import Driver
import logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration statements — grouped for readability. All idempotent.
# ---------------------------------------------------------------------------

# Uniqueness + existence constraints for PCG-owned labels.
_CONSTRAINTS: List[str] = [
    # Existing (also declared by pic_routes._init_schema; redeclared here for
    # single source of truth). IF NOT EXISTS makes these safe.
    "CREATE CONSTRAINT pic_identity IF NOT EXISTS FOR (i:Identity) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT pic_preference IF NOT EXISTS FOR (p:Preference) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT pic_goal IF NOT EXISTS FOR (g:Goal) REQUIRE g.id IS UNIQUE",
    "CREATE CONSTRAINT pic_observation IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE",
    # New PCG labels.
    "CREATE CONSTRAINT pcg_communication_style IF NOT EXISTS FOR (c:CommunicationStyle) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT pcg_workflow_run IF NOT EXISTS FOR (w:WorkflowRun) REQUIRE w.run_id IS UNIQUE",
    "CREATE CONSTRAINT pcg_insight IF NOT EXISTS FOR (i:Insight) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT pcg_email_draft IF NOT EXISTS FOR (d:EmailDraft) REQUIRE d.id IS UNIQUE",
]


# Range indexes for common query paths.
_RANGE_INDEXES: List[str] = [
    "CREATE INDEX pref_scope IF NOT EXISTS FOR (p:Preference) ON (p.scope)",
    "CREATE INDEX pref_category IF NOT EXISTS FOR (p:Preference) ON (p.category)",
    "CREATE INDEX pref_source IF NOT EXISTS FOR (p:Preference) ON (p.source)",
    "CREATE INDEX obs_status IF NOT EXISTS FOR (o:Observation) ON (o.status)",
    "CREATE INDEX obs_created_at IF NOT EXISTS FOR (o:Observation) ON (o.created_at)",
    "CREATE INDEX obs_category IF NOT EXISTS FOR (o:Observation) ON (o.category)",
    "CREATE INDEX comm_style_scope IF NOT EXISTS FOR (c:CommunicationStyle) ON (c.scope)",
    "CREATE INDEX workflow_run_name IF NOT EXISTS FOR (w:WorkflowRun) ON (w.workflow_name)",
    "CREATE INDEX workflow_run_started IF NOT EXISTS FOR (w:WorkflowRun) ON (w.started_at)",
    "CREATE INDEX workflow_run_status IF NOT EXISTS FOR (w:WorkflowRun) ON (w.status)",
    "CREATE INDEX insight_date IF NOT EXISTS FOR (i:Insight) ON (i.date)",
    "CREATE INDEX draft_status IF NOT EXISTS FOR (d:EmailDraft) ON (d.status)",
    "CREATE INDEX draft_created_at IF NOT EXISTS FOR (d:EmailDraft) ON (d.created_at)",
    "CREATE INDEX draft_scheduled_for IF NOT EXISTS FOR (d:EmailDraft) ON (d.scheduled_for)",
]


def _vector_index_stmt(name: str, label: str, dims: int) -> str:
    """Neo4j 5 native vector index. Cosine similarity."""
    return (
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
        f"FOR (n:{label}) ON (n.embedding) "
        "OPTIONS { indexConfig: { "
        f"`vector.dimensions`: {dims}, "
        "`vector.similarity_function`: 'cosine' } }"
    )


def _vector_indexes(dims: int) -> List[str]:
    return [
        _vector_index_stmt("preference_embedding", "Preference", dims),
        _vector_index_stmt("observation_embedding", "Observation", dims),
        _vector_index_stmt("communication_style_embedding", "CommunicationStyle", dims),
        _vector_index_stmt("insight_embedding", "Insight", dims),
    ]


# Additive property backfill for existing nodes so new code paths can assume
# default values are present. Uses coalesce so repeated runs are idempotent.
_BACKFILLS: List[str] = [
    # Preference defaults.
    "MATCH (p:Preference) "
    "SET p.scope = coalesce(p.scope, 'global'), "
    "    p.source = coalesce(p.source, 'explicit'), "
    "    p.confidence = coalesce(p.confidence, 1.0), "
    "    p.last_reinforced_at = coalesce(p.last_reinforced_at, p.created_at)",
    # Observation defaults. `status` = 'new' means not yet consolidated.
    "MATCH (o:Observation) "
    "SET o.status = coalesce(o.status, 'new'), "
    "    o.confidence = coalesce(o.confidence, 0.5)",
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_migrations(driver: Driver, embedding_dims: int = 768) -> dict:
    """
    Apply all idempotent migrations. Returns a summary dict for logging.

    Args:
        driver: Connected Neo4j driver.
        embedding_dims: Dimension of vectors stored in `embedding` fields.
            Must match the embedding model used by `embeddings.py`. Default
            768 matches `nomic-embed-text`. Changing this after data exists
            will NOT drop/recreate indexes automatically — the CREATE IF NOT
            EXISTS will silently skip. Drop by hand if you change models.

    Returns:
        {"constraints": N, "range_indexes": N, "vector_indexes": N, "backfills": N}
    """
    summary = {
        "constraints": 0,
        "range_indexes": 0,
        "vector_indexes": 0,
        "backfills": 0,
        "errors": [],
    }

    with driver.session() as session:
        for stmt in _CONSTRAINTS:
            try:
                session.run(stmt)
                summary["constraints"] += 1
            except Exception as e:  # noqa: BLE001 — log and continue
                summary["errors"].append(f"constraint: {stmt[:60]}... → {e}")
                log.warning("PCG migration constraint failed: %s", e)

        for stmt in _RANGE_INDEXES:
            try:
                session.run(stmt)
                summary["range_indexes"] += 1
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"range: {stmt[:60]}... → {e}")
                log.warning("PCG migration range index failed: %s", e)

        for stmt in _vector_indexes(embedding_dims):
            try:
                session.run(stmt)
                summary["vector_indexes"] += 1
            except Exception as e:  # noqa: BLE001
                # Vector indexes require Neo4j 5+. Log but don't fail startup.
                summary["errors"].append(f"vector: {stmt[:60]}... → {e}")
                log.warning(
                    "PCG vector index failed (Neo4j 5+ required for native "
                    "vector indexes): %s",
                    e,
                )

        for stmt in _BACKFILLS:
            try:
                session.run(stmt)
                summary["backfills"] += 1
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"backfill: {stmt[:60]}... → {e}")
                log.warning("PCG migration backfill failed: %s", e)

    log.info(
        "PCG migrations applied: %d constraints, %d range indexes, "
        "%d vector indexes, %d backfills (%d errors)",
        summary["constraints"],
        summary["range_indexes"],
        summary["vector_indexes"],
        summary["backfills"],
        len(summary["errors"]),
    )
    return summary
