"""Entity extraction using LLM via AI Gateway."""

import aiohttp
from typing import List
from config import get_settings
from models import Entity, Relationship, ExtractionResult, EntityType, RelationshipType

settings = get_settings()


class EntityExtractor:
    """Extract entities and relationships from text using LLM."""
    
    async def extract(self, text: str, document_id: str) -> ExtractionResult:
        """Extract entities and relationships from text."""
        # For now, return empty results - full LLM extraction can be added later
        # This prevents the service from failing to start
        return ExtractionResult(entities=[], relationships=[])


class CommunitySummarizer:
    """Summarize communities using LLM."""
    
    async def summarize_community(self, entities: List[Entity], relationships: List[Relationship]) -> str:
        """Generate a summary for a community."""
        # Simple summary based on entity types and counts
        if not entities:
            return "Empty community"
        
        type_counts = {}
        for entity in entities:
            type_key = entity.type.value
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
        
        summary_parts = [f"Community with {len(entities)} entities"]
        for entity_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            summary_parts.append(f"{count} {entity_type}")
        
        return ", ".join(summary_parts) + f" and {len(relationships)} relationships."
