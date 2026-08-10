"""Data models for Personal Context Graph (PCG)."""

from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


# =============================================================================
# ENUMS
# =============================================================================

class EntityType(str, Enum):
    """Entity types in the knowledge graph."""
    PERSON = "person"
    ORGANIZATION = "organization"
    CONCEPT = "concept"
    TECHNOLOGY = "technology"
    PROJECT = "project"
    DOCUMENT = "document"
    EVENT = "event"
    LOCATION = "location"
    SKILL = "skill"
    GOAL = "goal"
    OTHER = "other"


class RelationshipType(str, Enum):
    """Relationship types between entities."""
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    DEPENDS_ON = "depends_on"
    USES = "uses"
    CREATED_BY = "created_by"
    WORKS_AT = "works_at"
    LOCATED_IN = "located_in"
    KNOWS = "knows"
    SIMILAR_TO = "similar_to"
    LEADS_TO = "leads_to"
    REQUIRES = "requires"
    OTHER = "other"


# =============================================================================
# CORE MODELS
# =============================================================================

class Entity(BaseModel):
    """Entity in the knowledge graph."""
    id: str = Field(default="", description="Unique identifier")
    name: str = Field(..., description="Entity name")
    type: EntityType = Field(..., description="Entity type")
    description: Optional[str] = Field(None, description="Entity description")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Additional properties")
    source_document_id: Optional[str] = Field(None, description="Source document ID")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Relationship(BaseModel):
    """Relationship between entities."""
    id: str = Field(default="", description="Unique identifier")
    source_entity: str = Field(..., description="Source entity ID")
    target_entity: str = Field(..., description="Target entity ID")
    type: RelationshipType = Field(..., description="Relationship type")
    description: Optional[str] = Field(None, description="Relationship description")
    weight: float = Field(default=1.0, description="Relationship strength")
    properties: Dict[str, Any] = Field(default_factory=dict)
    source_document_id: Optional[str] = Field(None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Community(BaseModel):
    """Community of related entities."""
    id: str = Field(..., description="Community ID")
    name: str = Field(..., description="Community name")
    summary: str = Field(..., description="Community summary")
    entity_ids: List[str] = Field(default_factory=list)
    level: int = Field(default=0, description="Hierarchy level")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Document(BaseModel):
    """Document in the knowledge base."""
    id: str = Field(..., description="Document ID")
    title: str = Field(..., description="Document title")
    source: Optional[str] = Field(None, description="Document source")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class IngestRequest(BaseModel):
    """Request to ingest a document."""
    content: str = Field(..., description="Document content")
    title: Optional[str] = Field(None, description="Document title")
    source: Optional[str] = Field(None, description="Document source")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extract_entities: bool = Field(default=True, description="Whether to extract entities")


class IngestResponse(BaseModel):
    """Response from document ingestion."""
    document_id: str
    entities_extracted: int
    relationships_extracted: int
    chunks_created: int
    message: str


class SearchRequest(BaseModel):
    """Request for semantic search."""
    query: str = Field(..., description="Search query")
    entity_type: Optional[EntityType] = Field(None, description="Filter by entity type")
    limit: int = Field(default=10, ge=1, le=100)


class SearchResult(BaseModel):
    """Search result."""
    id: str
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """GraphRAG query request."""
    query: str = Field(..., description="Query text")
    mode: str = Field(default="hybrid", description="Query mode: local, global, or hybrid")


class GraphRAGResponse(BaseModel):
    """GraphRAG query response."""
    answer: str
    entities_used: List[Entity] = Field(default_factory=list)
    relationships_used: List[Relationship] = Field(default_factory=list)
    communities_used: List[Community] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


class EntityCreateRequest(BaseModel):
    """Request to create an entity."""
    name: str
    type: EntityType
    description: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class RelationshipCreateRequest(BaseModel):
    """Request to create a relationship."""
    source_entity: str
    target_entity: str
    type: RelationshipType
    description: Optional[str] = None
    weight: float = Field(default=1.0)
    properties: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# EXTRACTION MODELS
# =============================================================================

class ExtractionResult(BaseModel):
    """Result from entity extraction."""
    entities: List[Entity] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)


# =============================================================================
# PCG v2 MODELS — user-side subgraph (email personalization + agentification)
# =============================================================================
# These complement the existing :Identity / :Preference / :Goal / :Observation
# nodes managed by pic_routes.py. They introduce per-contact communication
# style, durable workflow audit, and daily-consolidation insights. All are
# PCG-owned; cross-subgraph edges into CIG labels (Person, Email, Topic, etc.)
# are written here but never mutate CIG node properties.


class PreferenceScope(str, Enum):
    """Scope of a preference. 'global' applies to all contexts; 'contact:<email>'
    applies only when interacting with that person; 'topic:<name>' applies only
    when the topic is active."""
    GLOBAL = "global"
    # contact:<email> and topic:<name> are free-form strings outside this enum;
    # validated at write time. Kept as Enum entry only for the default.


class PreferenceSource(str, Enum):
    """How a preference was acquired."""
    EXPLICIT = "explicit"        # user told us directly
    OBSERVED = "observed"        # derived from a single observation
    CONSOLIDATED = "consolidated"  # promoted from clustered observations


class ObservationStatus(str, Enum):
    """Lifecycle of an observation."""
    NEW = "new"            # recorded, not yet consolidated
    PROMOTED = "promoted"  # contributed to a Preference/CommunicationStyle
    DECAYED = "decayed"    # aged out without corroboration


class WorkflowRunStatus(str, Enum):
    """Hatchet-run outcome recorded in PCG audit."""
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EmailDraftStatus(str, Enum):
    """Lifecycle of an :EmailDraft node (PCG-owned; see Phase B2a)."""
    GENERATED = "generated"              # LLM just produced it
    PENDING_REVIEW = "pending_review"    # awaiting user review (PiCode/iOS)
    APPROVED = "approved"                # user approved, not yet sent
    SCHEDULED = "scheduled"              # approved + scheduled_for in the future
    SENT = "sent"                        # handed off to cig.send-email
    CANCELLED = "cancelled"              # user rejected, or expired


class CommunicationStyle(BaseModel):
    """Per-scope tone/voice the user uses when communicating.

    Populated by the daily-consolidation agent (Phase C1) from observed email
    drafts/replies, or directly by explicit user preferences. Queried at
    email-draft time to shape Hermes's output.

    The `embedding` field holds a vector of the style description so we can
    retrieve "similar style" for contacts we've never written to before.
    """
    id: str = Field(..., description="Unique identifier (style_<uuid>)")
    scope: str = Field(
        ..., description="'global' | 'contact:<email>' | 'topic:<name>'"
    )
    tone: Optional[str] = Field(
        None, description="e.g. 'warm-casual', 'direct-formal', 'terse'"
    )
    length: Optional[str] = Field(
        None, description="'short' | 'medium' | 'long'"
    )
    greeting: Optional[str] = Field(
        None, description="Preferred greeting phrase for this scope"
    )
    signoff: Optional[str] = Field(
        None, description="Preferred sign-off phrase for this scope"
    )
    description: Optional[str] = Field(
        None, description="Free-form style notes; used for embedding"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Agent's confidence; 1.0 for explicit user prefs"
    )
    source: PreferenceSource = Field(default=PreferenceSource.EXPLICIT)
    embedding: Optional[List[float]] = Field(
        None, description="Vector embedding of description (Neo4j vector index)"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowRun(BaseModel):
    """Durable audit of a Hatchet workflow run. Written by the workflow's
    final `record-workflow-run` activity (Phase B4). Linked via `:AUDITED_BY`
    to any PCG nodes the run wrote (Observation, Interaction, Preference, ...).
    """
    run_id: str = Field(..., description="Hatchet workflow run ID (stable)")
    workflow_name: str = Field(..., description="e.g. 'hermes-comms-sweep'")
    status: WorkflowRunStatus = Field(default=WorkflowRunStatus.RUNNING)
    triggered_by: Optional[str] = Field(
        None, description="'cron' | 'rpc' | 'nova' | user email"
    )
    dry_run: bool = Field(default=False)
    inputs_summary: Optional[str] = Field(
        None, description="Short human-readable summary of inputs"
    )
    outputs_summary: Optional[str] = Field(
        None, description="Short human-readable summary of outputs"
    )
    error: Optional[str] = Field(
        None, description="Stringified error if status=failed"
    )
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(None)
    metadata: Dict[str, Any] = Field(default_factory=dict)



class InsightType(str, Enum):
    """Type of consolidation insight (used by the Dreaming pipeline)."""
    PATTERN = "pattern"
    CONTRADICTION = "contradiction"
    TREND = "trend"


class WorkflowStatus(str, Enum):
    """Status of a PCG workflow run (used by consolidation)."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Insight(BaseModel):
    """Daily-consolidation output. Clusters recent observations and produces
    a human-readable summary + derived preferences/styles. Written by the
    `pcg.daily-consolidation` workflow (Phase C1). Linked via `:CONSOLIDATES`
    to its source observations and `:PROMOTED_TO` to any preferences/styles
    it generated.
    """
    id: str = Field(..., description="Unique identifier (insight_<uuid>)")
    date: str = Field(..., description="ISO date (YYYY-MM-DD) of the run")
    title: str = Field(..., description="Short human-readable title")
    summary: str = Field(..., description="Paragraph summary of the cluster")
    category: Optional[str] = Field(
        None, description="e.g. 'communication-style', 'scheduling', 'food'"
    )
    source_observation_ids: List[str] = Field(default_factory=list)
    promoted_preference_ids: List[str] = Field(default_factory=list)
    promoted_style_ids: List[str] = Field(default_factory=list)
    cluster_size: int = Field(default=0, ge=0)
    mean_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding: Optional[List[float]] = Field(None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Request/response models for new PCG routes (Phase A5)
# ---------------------------------------------------------------------------


class CommunicationStyleCreate(BaseModel):
    scope: str = Field(..., description="'global' | 'contact:<email>' | 'topic:<name>'")
    tone: Optional[str] = None
    length: Optional[str] = None
    greeting: Optional[str] = None
    signoff: Optional[str] = None
    description: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: PreferenceSource = Field(default=PreferenceSource.EXPLICIT)


class WorkflowRunCreate(BaseModel):
    run_id: str
    workflow_name: str
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    triggered_by: Optional[str] = None
    dry_run: bool = False
    inputs_summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRunUpdate(BaseModel):
    """Terminal update (success or failure) for a previously-started run."""
    status: WorkflowRunStatus
    outputs_summary: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContactContextResponse(BaseModel):
    """Response for GET /api/pcg/contact-context?email=<addr>.

    Joins the user's self-model (Identity + scoped Preferences +
    CommunicationStyle) with CIG's record of the counterparty (Person +
    recent Emails + Topics + Interactions) in one traversal. This is the
    payload that shapes Hermes's email-personalization prompt (Phase B2).
    """
    contact_email: str
    person: Optional[Dict[str, Any]] = None
    identity: Optional[Dict[str, Any]] = None
    scoped_preferences: List[Dict[str, Any]] = Field(default_factory=list)
    global_preferences: List[Dict[str, Any]] = Field(default_factory=list)
    communication_styles: List[Dict[str, Any]] = Field(default_factory=list)
    recent_emails: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Up to 10 most recent emails between user and contact",
    )
    recent_topics: List[str] = Field(default_factory=list)
    recent_observations: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# EmailDraft — PCG-owned draft lifecycle (Phase B2a)
# ---------------------------------------------------------------------------
# A draft is a user-workflow artifact (not yet an email in the world), so it
# belongs in the PCG user-side subgraph. Cross-subgraph edge :DRAFTS_FOR
# points at the CIG :Email being replied to. Once the user approves and the
# workflow dispatches cig.send-email, CIG creates an :OutboundEmail node and
# this draft is marked status=sent. PCG never writes to :OutboundEmail.


class EmailDraft(BaseModel):
    """An in-flight email reply awaiting review + approval + send."""
    id: str = Field(..., description="Unique identifier (draft_<uuid>)")
    run_id: Optional[str] = Field(
        None, description="Hatchet workflow run_id that created this draft"
    )
    in_reply_to_email_id: Optional[str] = Field(
        None, description="CIG :Email.id being replied to (linked via :DRAFTS_FOR)"
    )
    to_email: str = Field(..., description="Recipient email address")
    to_name: Optional[str] = None
    cc_emails: List[str] = Field(default_factory=list)
    bcc_emails: List[str] = Field(default_factory=list)
    subject: str = Field(..., description="Subject line")
    body: str = Field(..., description="Plain-text body (markdown OK)")
    status: EmailDraftStatus = Field(default=EmailDraftStatus.GENERATED)
    # Review/approval metadata — which surface produced/approved it.
    drafter_agent: Optional[str] = Field(
        None, description="e.g. 'hermes' — the agent that wrote this draft"
    )
    review_channel: Optional[str] = Field(
        None, description="'picode' | 'ios' | 'voice' — where review UI lives"
    )
    approval_id: Optional[str] = Field(
        None, description="approval-service request id (once review passes)"
    )
    scheduled_for: Optional[datetime] = Field(
        None, description="If set, workflow sleeps until this time before send"
    )
    sent_message_id: Optional[str] = Field(
        None, description="SMTP/Gmail message id once sent"
    )
    user_edits: int = Field(
        default=0,
        description="Number of times the user edited the body before approval",
    )
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EmailDraftCreate(BaseModel):
    """Payload for POST /api/pcg/drafts — called by Hermes draft step."""
    run_id: Optional[str] = None
    in_reply_to_email_id: Optional[str] = None
    to_email: str
    to_name: Optional[str] = None
    cc_emails: List[str] = Field(default_factory=list)
    bcc_emails: List[str] = Field(default_factory=list)
    subject: str
    body: str
    drafter_agent: Optional[str] = "hermes"
    review_channel: Optional[str] = "picode"
    scheduled_for: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EmailDraftUpdate(BaseModel):
    """Payload for PATCH /api/pcg/drafts/{id} — user edits / status changes.
    All fields optional; only provided fields are updated."""
    subject: Optional[str] = None
    body: Optional[str] = None
    cc_emails: Optional[List[str]] = None
    bcc_emails: Optional[List[str]] = None
    status: Optional[EmailDraftStatus] = None
    scheduled_for: Optional[datetime] = None
    approval_id: Optional[str] = None
    sent_message_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # Flag the server to increment user_edits when body/subject changed by
    # a human review surface (PiCode / iOS), not by an automated step.
    is_user_edit: bool = False
