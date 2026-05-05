"""Neo4j graph store for knowledge graph and PCG user-side subgraph.

This module owns all PCG writes. Ownership rules (see
services/personal-kg/AGENTIFICATION_PLAN.md):
  - PCG labels (Identity, Preference, Goal, Observation, Entity,
    CommunicationStyle, WorkflowRun, Insight) — this store writes freely.
  - CIG labels (Email, Person, Thread, Topic, Contact, Interaction, ...) —
    this store only MATCHes them. Never MERGE/SET/DELETE CIG nodes.
  - Cross-subgraph edges (APPLIES_TO, DERIVED_FROM, STYLE_FOR, AUDITED_BY,
    PROMOTED_TO, CONSOLIDATES) are PCG-owned and direction is always
    PCG → CIG. Edge property `owner='pcg'` is stamped for audit.
"""

import uuid
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from neo4j import GraphDatabase
from config import get_settings
from models import (
    Entity, Relationship, Community, EntityType, RelationshipType,
    CommunicationStyle, WorkflowRun, WorkflowRunStatus, Insight,
    PreferenceSource, EmailDraft, EmailDraftStatus,
)

settings = get_settings()


class GraphStore:
    """Neo4j graph database store."""
    
    def __init__(self):
        self.driver = None
        self.settings = settings
    
    def connect(self):
        """Connect to Neo4j."""
        self.driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        )
        
        with self.driver.session() as session:
            session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
            session.run("CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
            session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")
    
    def close(self):
        """Close connection."""
        if self.driver:
            self.driver.close()
    
    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WITH count(e) as entities
                MATCH ()-[r]->()
                WITH entities, count(r) as relationships
                MATCH (c:Community)
                RETURN entities, relationships, count(c) as communities
            """)
            record = result.single()
            if record:
                return {
                    "entities": record["entities"],
                    "relationships": record["relationships"],
                    "communities": record["communities"]
                }
            return {"entities": 0, "relationships": 0, "communities": 0}
    
    def create_document(self, doc_id: str, title: str, source: Optional[str], metadata: Dict[str, Any]):
        """Create a document node."""
        with self.driver.session() as session:
            session.run("""
                CREATE (d:Document {
                    id: $id,
                    title: $title,
                    source: $source,
                    metadata: $metadata,
                    created_at: datetime()
                })
            """, id=doc_id, title=title, source=source, metadata=metadata)
    
    def upsert_entity(self, entity: Entity) -> Entity:
        """Create or update an entity."""
        if not entity.id:
            entity.id = f"entity_{uuid.uuid4().hex[:12]}"
        
        with self.driver.session() as session:
            session.run("""
                MERGE (e:Entity {id: $id})
                ON CREATE SET 
                    e.created_at = datetime(),
                    e.name = $name,
                    e.type = $type,
                    e.description = $description,
                    e.properties = $properties,
                    e.source_document_id = $source_document_id,
                    e.updated_at = datetime()
                ON MATCH SET 
                    e.name = $name,
                    e.type = $type,
                    e.description = $description,
                    e.properties = $properties,
                    e.source_document_id = $source_document_id,
                    e.updated_at = datetime()
            """, 
                id=entity.id,
                name=entity.name,
                type=entity.type.value,
                description=entity.description,
                properties=json.dumps(entity.properties),
                source_document_id=entity.source_document_id
            )
        
        return entity
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $id})
                RETURN e
            """, id=entity_id)
            
            record = result.single()
            if record:
                node = record["e"]
                return Entity(
                    id=node["id"],
                    name=node["name"],
                    type=EntityType(node["type"]),
                    description=node.get("description"),
                    properties=node.get("properties", {}),
                    source_document_id=node.get("source_document_id")
                )
            return None
    
    def list_entities(self, entity_type: Optional[EntityType] = None, limit: int = 50, offset: int = 0) -> List[Entity]:
        """List entities with optional filtering."""
        with self.driver.session() as session:
            if entity_type:
                result = session.run("""
                    MATCH (e:Entity {type: $type})
                    RETURN e
                    ORDER BY e.created_at DESC
                    SKIP $offset
                    LIMIT $limit
                """, type=entity_type.value, offset=offset, limit=limit)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    RETURN e
                    ORDER BY e.created_at DESC
                    SKIP $offset
                    LIMIT $limit
                """, offset=offset, limit=limit)
            
            entities = []
            for record in result:
                node = record["e"]
                try:
                    node_type = EntityType(node["type"])
                except ValueError:
                    node_type = EntityType.OTHER
                
                entities.append(Entity(
                    id=node.get("id", f"entity_{uuid.uuid4().hex[:12]}"),
                    name=node["name"],
                    type=node_type,
                    description=node.get("description"),
                    properties=node.get("properties", {}),
                    source_document_id=node.get("source_document_id")
                ))
            return entities
    
    def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $id})
                DETACH DELETE e
                RETURN count(e) as deleted
            """, id=entity_id)
            record = result.single()
            return record["deleted"] > 0 if record else False
    
    def create_relationship(self, relationship: Relationship) -> Relationship:
        """Create a relationship between entities."""
        if not relationship.id:
            relationship.id = f"rel_{uuid.uuid4().hex[:12]}"
        
        with self.driver.session() as session:
            session.run("""
                MATCH (source:Entity {id: $source_id})
                MATCH (target:Entity {id: $target_id})
                CREATE (source)-[r:RELATES {
                    id: $id,
                    type: $type,
                    description: $description,
                    weight: $weight,
                    properties: $properties,
                    source_document_id: $source_document_id,
                    created_at: datetime()
                }]->(target)
            """,
                id=relationship.id,
                source_id=relationship.source_entity,
                target_id=relationship.target_entity,
                type=relationship.type.value,
                description=relationship.description,
                weight=relationship.weight,
                properties=json.dumps(relationship.properties),
                source_document_id=relationship.source_document_id
            )
        
        return relationship
    
    def get_relationships(self, entity_id: Optional[str] = None, relationship_type: Optional[RelationshipType] = None) -> List[Relationship]:
        """Get relationships, optionally filtered."""
        with self.driver.session() as session:
            if entity_id:
                result = session.run("""
                    MATCH (source:Entity {id: $entity_id})-[r:RELATES]->(target:Entity)
                    RETURN r, source.id as source_id, target.id as target_id
                """, entity_id=entity_id)
            else:
                result = session.run("""
                    MATCH (source:Entity)-[r:RELATES]->(target:Entity)
                    RETURN r, source.id as source_id, target.id as target_id
                    LIMIT 1000
                """)
            
            relationships = []
            for record in result:
                rel = record["r"]
                rel_type = RelationshipType(rel["type"]) if "type" in rel else RelationshipType.OTHER
                
                if relationship_type and rel_type != relationship_type:
                    continue
                
                relationships.append(Relationship(
                    id=rel.get("id", ""),
                    source_entity=record["source_id"],
                    target_entity=record["target_id"],
                    type=rel_type,
                    description=rel.get("description"),
                    weight=rel.get("weight", 1.0),
                    properties=rel.get("properties", {}),
                    source_document_id=rel.get("source_document_id")
                ))
            return relationships
    
    def link_entity_to_document(self, entity_id: str, document_id: str):
        """Link an entity to its source document."""
        with self.driver.session() as session:
            session.run("""
                MATCH (e:Entity {id: $entity_id})
                MATCH (d:Document {id: $document_id})
                MERGE (e)-[:EXTRACTED_FROM]->(d)
            """, entity_id=entity_id, document_id=document_id)
    
    def get_entity_neighbors(self, entity_id: str, depth: int = 1) -> Dict[str, Any]:
        """Get an entity and its neighbors."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $entity_id})
                OPTIONAL MATCH (e)-[*1..$depth]-(neighbor:Entity)
                RETURN e, collect(DISTINCT neighbor) as neighbors
            """, entity_id=entity_id, depth=depth)
            
            record = result.single()
            if not record:
                return {"entity": None, "neighbors": [], "neighbor_count": 0}
            
            entity_node = record["e"]
            entity = Entity(
                id=entity_node["id"],
                name=entity_node["name"],
                type=EntityType(entity_node["type"]),
                description=entity_node.get("description"),
                properties=entity_node.get("properties", {})
            )
            
            neighbors = []
            for neighbor_node in record["neighbors"]:
                if neighbor_node:
                    neighbors.append(Entity(
                        id=neighbor_node["id"],
                        name=neighbor_node["name"],
                        type=EntityType(neighbor_node["type"]),
                        description=neighbor_node.get("description"),
                        properties=neighbor_node.get("properties", {})
                    ))
            
            return {
                "entity": entity,
                "neighbors": neighbors,
                "neighbor_count": len(neighbors)
            }
    
    def create_community(self, community: Community):
        """Create a community."""
        with self.driver.session() as session:
            session.run("""
                CREATE (c:Community {
                    id: $id,
                    name: $name,
                    summary: $summary,
                    entity_ids: $entity_ids,
                    level: $level,
                    created_at: datetime()
                })
            """,
                id=community.id,
                name=community.name,
                summary=community.summary,
                entity_ids=community.entity_ids,
                level=community.level
            )
    
    def get_communities(self, level: Optional[int] = None) -> List[Community]:
        """Get communities."""
        with self.driver.session() as session:
            if level is not None:
                result = session.run("""
                    MATCH (c:Community {level: $level})
                    RETURN c
                """, level=level)
            else:
                result = session.run("""
                    MATCH (c:Community)
                    RETURN c
                """)
            
            communities = []
            for record in result:
                node = record["c"]
                communities.append(Community(
                    id=node["id"],
                    name=node["name"],
                    summary=node["summary"],
                    entity_ids=node.get("entity_ids", []),
                    level=node.get("level", 0)
                ))
            return communities

    # =========================================================================
    # PCG v2 — CommunicationStyle, WorkflowRun, Insight (Phase A3)
    # =========================================================================
    # All methods below write PCG-owned labels or PCG-owned cross-subgraph
    # edges into CIG labels. CIG node properties are never mutated.

    # ---- CommunicationStyle ------------------------------------------------

    def upsert_communication_style(
        self, style: CommunicationStyle
    ) -> CommunicationStyle:
        """Create or update a communication style. Keyed by `scope` so each
        scope (global / contact:<email> / topic:<name>) has exactly one style.
        """
        if not style.id:
            style.id = f"style_{uuid.uuid4().hex[:12]}"
        with self.driver.session() as session:
            session.run(
                """
                MERGE (c:CommunicationStyle {scope: $scope})
                ON CREATE SET c.id = $id, c.created_at = datetime()
                SET c.tone = $tone,
                    c.length = $length,
                    c.greeting = $greeting,
                    c.signoff = $signoff,
                    c.description = $description,
                    c.confidence = $confidence,
                    c.source = $source,
                    c.embedding = $embedding,
                    c.updated_at = datetime()
                """,
                id=style.id,
                scope=style.scope,
                tone=style.tone,
                length=style.length,
                greeting=style.greeting,
                signoff=style.signoff,
                description=style.description,
                confidence=style.confidence,
                source=(
                    style.source.value
                    if hasattr(style.source, "value")
                    else style.source
                ),
                embedding=style.embedding,
            )
        return style

    def get_communication_style(self, scope: str) -> Optional[Dict[str, Any]]:
        """Get the communication style for a scope (e.g. 'contact:alice@x.com')."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (c:CommunicationStyle {scope: $scope}) RETURN c",
                scope=scope,
            )
            record = result.single()
            return dict(record["c"]) if record else None

    def list_communication_styles(
        self, scope_prefix: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List styles, optionally filtered by scope prefix (e.g. 'contact:')."""
        with self.driver.session() as session:
            if scope_prefix:
                result = session.run(
                    """
                    MATCH (c:CommunicationStyle)
                    WHERE c.scope STARTS WITH $prefix
                    RETURN c
                    ORDER BY c.updated_at DESC
                    LIMIT $limit
                    """,
                    prefix=scope_prefix,
                    limit=limit,
                )
            else:
                result = session.run(
                    """
                    MATCH (c:CommunicationStyle)
                    RETURN c
                    ORDER BY c.updated_at DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
            return [dict(r["c"]) for r in result]

    def link_style_to_person(self, style_id: str, person_email: str) -> bool:
        """Create PCG-owned :STYLE_FOR edge from CommunicationStyle to CIG
        :Person. MATCH-only on the Person node; never mutates it.
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (c:CommunicationStyle {id: $style_id})
                MATCH (p:Person {email: $email})
                MERGE (c)-[r:STYLE_FOR]->(p)
                ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                RETURN r IS NOT NULL AS linked
                """,
                style_id=style_id,
                email=person_email,
            )
            record = result.single()
            return bool(record and record["linked"])

    # ---- WorkflowRun (Hatchet audit) --------------------------------------

    def upsert_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        """Create or start a workflow-run audit node. Called at workflow start
        (status='running') and again at completion (status='succeeded' or
        'failed') via update_workflow_run_terminal.
        """
        with self.driver.session() as session:
            session.run(
                """
                MERGE (w:WorkflowRun {run_id: $run_id})
                ON CREATE SET w.started_at = datetime(),
                              w.created_at = datetime()
                SET w.workflow_name = $workflow_name,
                    w.status = $status,
                    w.triggered_by = $triggered_by,
                    w.dry_run = $dry_run,
                    w.inputs_summary = $inputs_summary,
                    w.metadata = $metadata,
                    w.updated_at = datetime()
                """,
                run_id=run.run_id,
                workflow_name=run.workflow_name,
                status=run.status.value if hasattr(run.status, "value") else run.status,
                triggered_by=run.triggered_by,
                dry_run=run.dry_run,
                inputs_summary=run.inputs_summary,
                metadata=run.metadata or {},
            )
        return run

    def update_workflow_run_terminal(
        self,
        run_id: str,
        status: WorkflowRunStatus,
        outputs_summary: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a workflow run as terminal (succeeded/failed/cancelled).
        Sets completed_at. Idempotent re-calls overwrite."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (w:WorkflowRun {run_id: $run_id})
                SET w.status = $status,
                    w.outputs_summary = coalesce($outputs_summary, w.outputs_summary),
                    w.error = coalesce($error, w.error),
                    w.metadata = coalesce($metadata, w.metadata),
                    w.completed_at = datetime(),
                    w.updated_at = datetime()
                RETURN count(w) AS updated
                """,
                run_id=run_id,
                status=status.value if hasattr(status, "value") else status,
                outputs_summary=outputs_summary,
                error=error,
                metadata=metadata,
            )
            record = result.single()
            return bool(record and record["updated"] > 0)

    def link_audited_by(self, node_label: str, node_id_key: str,
                        node_id_value: str, run_id: str) -> bool:
        """Link any PCG node to a WorkflowRun via :AUDITED_BY. Used by
        workflows to stamp every node they wrote with the run that wrote it.
        `node_label` is interpolated (not parameterizable in Cypher labels);
        caller must pass a trusted PCG-owned label.
        """
        allowed = {
            "Observation", "Preference", "CommunicationStyle",
            "Insight", "Goal", "Interaction",
        }
        if node_label not in allowed:
            raise ValueError(f"link_audited_by: label '{node_label}' not in allow-list")
        with self.driver.session() as session:
            result = session.run(
                f"""
                MATCH (n:{node_label} {{{node_id_key}: $node_id_value}})
                MATCH (w:WorkflowRun {{run_id: $run_id}})
                MERGE (n)-[r:AUDITED_BY]->(w)
                ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                RETURN r IS NOT NULL AS linked
                """,
                node_id_value=node_id_value,
                run_id=run_id,
            )
            record = result.single()
            return bool(record and record["linked"])

    # ---- Insight (daily consolidation output, Phase C1) -------------------

    def create_insight(self, insight: Insight) -> Insight:
        """Create a daily-consolidation insight node."""
        if not insight.id:
            insight.id = f"insight_{uuid.uuid4().hex[:12]}"
        with self.driver.session() as session:
            session.run(
                """
                CREATE (i:Insight {
                    id: $id,
                    date: $date,
                    title: $title,
                    summary: $summary,
                    category: $category,
                    source_observation_ids: $source_observation_ids,
                    promoted_preference_ids: $promoted_preference_ids,
                    promoted_style_ids: $promoted_style_ids,
                    cluster_size: $cluster_size,
                    mean_confidence: $mean_confidence,
                    embedding: $embedding,
                    created_at: datetime()
                })
                """,
                id=insight.id,
                date=insight.date,
                title=insight.title,
                summary=insight.summary,
                category=insight.category,
                source_observation_ids=insight.source_observation_ids,
                promoted_preference_ids=insight.promoted_preference_ids,
                promoted_style_ids=insight.promoted_style_ids,
                cluster_size=insight.cluster_size,
                mean_confidence=insight.mean_confidence,
                embedding=insight.embedding,
            )
            # Link :CONSOLIDATES edges to source observations.
            if insight.source_observation_ids:
                session.run(
                    """
                    MATCH (i:Insight {id: $insight_id})
                    UNWIND $obs_ids AS obs_id
                    MATCH (o:Observation {id: obs_id})
                    MERGE (i)-[r:CONSOLIDATES]->(o)
                    ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                    """,
                    insight_id=insight.id,
                    obs_ids=insight.source_observation_ids,
                )
            # Mark promoted observations as 'promoted' status.
            if insight.source_observation_ids and (
                insight.promoted_preference_ids or insight.promoted_style_ids
            ):
                session.run(
                    """
                    UNWIND $obs_ids AS obs_id
                    MATCH (o:Observation {id: obs_id})
                    SET o.status = 'promoted', o.updated_at = datetime()
                    """,
                    obs_ids=insight.source_observation_ids,
                )
        return insight

    def list_recent_insights(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (i:Insight)
                RETURN i
                ORDER BY i.created_at DESC
                LIMIT $limit
                """,
                limit=limit,
            )
            return [dict(r["i"]) for r in result]

    def list_insights_by_date(self, date: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (i:Insight {date: $date})
                RETURN i
                ORDER BY i.created_at DESC
                """,
                date=date,
            )
            return [dict(r["i"]) for r in result]

    # ---- Observation helpers for consolidation (Phase C1) ------------------

    def list_observations_since(
        self, since_iso: str, status: str = "new", limit: int = 500
    ) -> List[Dict[str, Any]]:
        """List observations newer than `since_iso` with given status. Used
        by the nightly consolidation workflow."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (o:Observation)
                WHERE o.status = $status
                  AND o.created_at >= datetime($since_iso)
                RETURN o
                ORDER BY o.created_at DESC
                LIMIT $limit
                """,
                since_iso=since_iso,
                status=status,
                limit=limit,
            )
            return [dict(r["o"]) for r in result]

    def decay_stale_observations(self, older_than_iso: str) -> int:
        """Mark 'new' observations older than the given timestamp as 'decayed'.
        Returns count updated."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (o:Observation)
                WHERE o.status = 'new'
                  AND o.created_at < datetime($cutoff)
                SET o.status = 'decayed', o.updated_at = datetime()
                RETURN count(o) AS decayed
                """,
                cutoff=older_than_iso,
            )
            record = result.single()
            return int(record["decayed"]) if record else 0

    def link_observation_to_email(
        self, observation_id: str, email_id: str
    ) -> bool:
        """Create PCG-owned :DERIVED_FROM edge from Observation to CIG :Email.
        MATCH-only on the Email node; never mutates CIG data.
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (o:Observation {id: $obs_id})
                MATCH (e:Email {id: $email_id})
                MERGE (o)-[r:DERIVED_FROM]->(e)
                ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                RETURN r IS NOT NULL AS linked
                """,
                obs_id=observation_id,
                email_id=email_id,
            )
            record = result.single()
            return bool(record and record["linked"])

    def link_preference_to_person(
        self, preference_id: str, person_email: str
    ) -> bool:
        """Scope a Preference to a specific Person via :APPLIES_TO."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (p:Preference {id: $pref_id})
                MATCH (person:Person {email: $email})
                MERGE (p)-[r:APPLIES_TO]->(person)
                ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                RETURN r IS NOT NULL AS linked
                """,
                pref_id=preference_id,
                email=person_email,
            )
            record = result.single()
            return bool(record and record["linked"])

    def link_observation_promoted_to(
        self, observation_id: str, target_label: str, target_id: str
    ) -> bool:
        """Provenance edge: Observation → (Preference | CommunicationStyle)
        that was derived from it. Audit trail for the consolidation agent."""
        if target_label not in {"Preference", "CommunicationStyle"}:
            raise ValueError(
                f"link_observation_promoted_to: target label "
                f"'{target_label}' not allowed"
            )
        with self.driver.session() as session:
            result = session.run(
                f"""
                MATCH (o:Observation {{id: $obs_id}})
                MATCH (t:{target_label} {{id: $target_id}})
                MERGE (o)-[r:PROMOTED_TO]->(t)
                ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                RETURN r IS NOT NULL AS linked
                """,
                obs_id=observation_id,
                target_id=target_id,
            )
            record = result.single()
            return bool(record and record["linked"])

    # =========================================================================
    # EmailDraft CRUD (Phase B2a)
    # =========================================================================
    # :EmailDraft is PCG-owned — a draft is a user-workflow artifact not yet
    # an email in the world. Cross-subgraph edge :DRAFTS_FOR points at the
    # CIG :Email being replied to (MATCH-only on the Email).

    def create_email_draft(self, draft: EmailDraft) -> EmailDraft:
        """Create a new draft node. If `in_reply_to_email_id` is set and
        resolvable, also creates the :DRAFTS_FOR edge to CIG :Email."""
        if not draft.id:
            draft.id = f"draft_{uuid.uuid4().hex[:12]}"
        with self.driver.session() as session:
            session.run(
                """
                CREATE (d:EmailDraft {
                    id: $id,
                    run_id: $run_id,
                    in_reply_to_email_id: $in_reply_to_email_id,
                    to_email: $to_email,
                    to_name: $to_name,
                    cc_emails: $cc_emails,
                    bcc_emails: $bcc_emails,
                    subject: $subject,
                    body: $body,
                    status: $status,
                    drafter_agent: $drafter_agent,
                    review_channel: $review_channel,
                    approval_id: $approval_id,
                    scheduled_for: $scheduled_for,
                    sent_message_id: $sent_message_id,
                    user_edits: $user_edits,
                    rejection_reason: $rejection_reason,
                    metadata: $metadata,
                    created_at: datetime(),
                    updated_at: datetime()
                })
                """,
                id=draft.id,
                run_id=draft.run_id,
                in_reply_to_email_id=draft.in_reply_to_email_id,
                to_email=draft.to_email,
                to_name=draft.to_name,
                cc_emails=draft.cc_emails,
                bcc_emails=draft.bcc_emails,
                subject=draft.subject,
                body=draft.body,
                status=(
                    draft.status.value
                    if hasattr(draft.status, "value")
                    else draft.status
                ),
                drafter_agent=draft.drafter_agent,
                review_channel=draft.review_channel,
                approval_id=draft.approval_id,
                scheduled_for=(
                    draft.scheduled_for.isoformat()
                    if draft.scheduled_for
                    else None
                ),
                sent_message_id=draft.sent_message_id,
                user_edits=draft.user_edits,
                rejection_reason=draft.rejection_reason,
                metadata=draft.metadata or {},
            )
            # Cross-subgraph edge into CIG. MATCH-only: never mutate :Email.
            if draft.in_reply_to_email_id:
                session.run(
                    """
                    MATCH (d:EmailDraft {id: $draft_id})
                    MATCH (e:Email {id: $email_id})
                    MERGE (d)-[r:DRAFTS_FOR]->(e)
                    ON CREATE SET r.owner = 'pcg', r.created_at = datetime()
                    """,
                    draft_id=draft.id,
                    email_id=draft.in_reply_to_email_id,
                )
        return draft

    def get_email_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                "MATCH (d:EmailDraft {id: $id}) RETURN d", id=draft_id
            )
            record = result.single()
            return dict(record["d"]) if record else None

    def list_email_drafts(
        self,
        status: Optional[str] = None,
        review_channel: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List drafts, filterable by status and review channel. Used by
        PiCode's 'drafts pending review' inbox view."""
        with self.driver.session() as session:
            clauses: List[str] = []
            params: Dict[str, Any] = {"limit": limit}
            if status:
                clauses.append("d.status = $status")
                params["status"] = status
            if review_channel:
                clauses.append("d.review_channel = $review_channel")
                params["review_channel"] = review_channel
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            result = session.run(
                f"""
                MATCH (d:EmailDraft)
                {where}
                RETURN d
                ORDER BY d.created_at DESC
                LIMIT $limit
                """,
                **params,
            )
            return [dict(r["d"]) for r in result]

    def update_email_draft(
        self,
        draft_id: str,
        updates: Dict[str, Any],
        is_user_edit: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Patch a draft. Only provided keys are updated. If `is_user_edit`
        is True AND body/subject changed, user_edits is incremented.
        Returns the updated draft dict, or None if not found.
        """
        # Normalize datetime fields.
        if "scheduled_for" in updates and isinstance(
            updates["scheduled_for"], datetime
        ):
            updates["scheduled_for"] = updates["scheduled_for"].isoformat()
        # Normalize enum.
        if "status" in updates and hasattr(updates["status"], "value"):
            updates["status"] = updates["status"].value

        with self.driver.session() as session:
            # Read-modify-compute-write so we can bump user_edits correctly.
            existing = session.run(
                "MATCH (d:EmailDraft {id: $id}) RETURN d", id=draft_id
            ).single()
            if not existing:
                return None
            existing_d = dict(existing["d"])

            bump_edits = False
            if is_user_edit:
                if "body" in updates and updates["body"] != existing_d.get("body"):
                    bump_edits = True
                if (
                    "subject" in updates
                    and updates["subject"] != existing_d.get("subject")
                ):
                    bump_edits = True

            set_fragments = []
            params: Dict[str, Any] = {"id": draft_id}
            for key, value in updates.items():
                set_fragments.append(f"d.{key} = ${key}")
                params[key] = value
            if bump_edits:
                set_fragments.append("d.user_edits = coalesce(d.user_edits,0) + 1")
            set_fragments.append("d.updated_at = datetime()")

            set_clause = ", ".join(set_fragments)
            result = session.run(
                f"""
                MATCH (d:EmailDraft {{id: $id}})
                SET {set_clause}
                RETURN d
                """,
                **params,
            )
            record = result.single()
            return dict(record["d"]) if record else None

    # =========================================================================
    # THE cross-subgraph traversal — the reason this whole design exists.
    # =========================================================================

    def get_contact_context(
        self, contact_email: str, email_limit: int = 10,
        observation_limit: int = 10, topic_limit: int = 5,
    ) -> Dict[str, Any]:
        """Join PCG user-side (Identity + scoped Preferences + Communication
        styles + recent Observations) with CIG world-side (Person + recent
        Emails + Topics) for a single contact. Powers `GET /api/pcg/contact-
        context` and the email-personalization prompt injection in Phase B2.

        Everything is a READ. No mutations. CIG nodes are only MATCHed.

        Returns a dict matching the `ContactContextResponse` shape.
        """
        with self.driver.session() as session:
            # Identity + global preferences + communication styles (user-side).
            identity_result = session.run("MATCH (i:Identity) RETURN i LIMIT 1")
            identity_rec = identity_result.single()
            identity = dict(identity_rec["i"]) if identity_rec else None

            global_prefs_result = session.run(
                """
                MATCH (p:Preference)
                WHERE coalesce(p.scope, 'global') = 'global'
                RETURN p ORDER BY p.confidence DESC, p.created_at DESC LIMIT 50
                """
            )
            global_prefs = [dict(r["p"]) for r in global_prefs_result]

            scoped_prefs_result = session.run(
                """
                MATCH (p:Preference)
                WHERE p.scope = $scope
                RETURN p ORDER BY p.confidence DESC, p.created_at DESC
                """,
                scope=f"contact:{contact_email}",
            )
            scoped_prefs = [dict(r["p"]) for r in scoped_prefs_result]

            styles_result = session.run(
                """
                MATCH (c:CommunicationStyle)
                WHERE c.scope IN ['global', $scope]
                RETURN c ORDER BY
                    CASE WHEN c.scope = $scope THEN 0 ELSE 1 END,
                    c.confidence DESC
                """,
                scope=f"contact:{contact_email}",
            )
            styles = [dict(r["c"]) for r in styles_result]

            # Person + recent emails + topics (CIG world-side, READ-ONLY).
            # Person is matched by email. Emails are joined via the existing
            # CIG SENT / SENT_TO edges (Person-Email direction). We pull the
            # N most recent regardless of direction to capture two-way history.
            person_result = session.run(
                "MATCH (p:Person {email: $email}) RETURN p LIMIT 1",
                email=contact_email,
            )
            person_rec = person_result.single()
            person = dict(person_rec["p"]) if person_rec else None

            recent_emails: List[Dict[str, Any]] = []
            recent_topics: List[str] = []
            if person is not None:
                emails_result = session.run(
                    """
                    MATCH (p:Person {email: $email})
                    OPTIONAL MATCH (p)-[:SENT|SENT_TO]-(e:Email)
                    WITH e
                    WHERE e IS NOT NULL
                    RETURN e
                    ORDER BY e.date DESC
                    LIMIT $limit
                    """,
                    email=contact_email,
                    limit=email_limit,
                )
                for r in emails_result:
                    e = r["e"]
                    if e is None:
                        continue
                    recent_emails.append({
                        "id": e.get("id"),
                        "subject": e.get("subject"),
                        "date": str(e.get("date")) if e.get("date") else None,
                        "category": e.get("category"),
                        "is_sent": e.get("is_sent"),
                        "thread_id": e.get("thread_id"),
                    })

                topics_result = session.run(
                    """
                    MATCH (p:Person {email: $email})-[:SENT|SENT_TO]-(e:Email)
                          -[:HAS_TOPIC]->(t:Topic)
                    RETURN t.name AS name, count(*) AS freq
                    ORDER BY freq DESC
                    LIMIT $limit
                    """,
                    email=contact_email,
                    limit=topic_limit,
                )
                recent_topics = [r["name"] for r in topics_result if r["name"]]

            # Observations derived from emails with this contact.
            obs_result = session.run(
                """
                MATCH (o:Observation)-[:DERIVED_FROM]->(e:Email)
                      -[:SENT|SENT_TO]-(:Person {email: $email})
                WHERE o.status IN ['new', 'promoted']
                RETURN o
                ORDER BY o.created_at DESC
                LIMIT $limit
                """,
                email=contact_email,
                limit=observation_limit,
            )
            recent_observations = [dict(r["o"]) for r in obs_result]

            stats = {
                "global_preferences": len(global_prefs),
                "scoped_preferences": len(scoped_prefs),
                "communication_styles": len(styles),
                "recent_emails": len(recent_emails),
                "recent_topics": len(recent_topics),
                "recent_observations": len(recent_observations),
            }

            return {
                "contact_email": contact_email,
                "person": person,
                "identity": identity,
                "scoped_preferences": scoped_prefs,
                "global_preferences": global_prefs,
                "communication_styles": styles,
                "recent_emails": recent_emails,
                "recent_topics": recent_topics,
                "recent_observations": recent_observations,
                "stats": stats,
            }
