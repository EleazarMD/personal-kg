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
