"""ChromaDB vector store for semantic search."""

import chromadb
from typing import List, Optional
from config import get_settings
from models import Entity, SearchResult

settings = get_settings()


class VectorStore:
    """ChromaDB vector database store."""
    
    def __init__(self):
        self.client = None
        self.entities_collection = None
        self.chunks_collection = None
        self.communities_collection = None
        self.settings = settings
    
    def connect(self):
        """Connect to ChromaDB."""
        self.client = chromadb.PersistentClient(path=self.settings.chromadb_path)
        
        self.entities_collection = self.client.get_or_create_collection(
            name="entities",
            metadata={"description": "Entity embeddings"}
        )
        
        self.chunks_collection = self.client.get_or_create_collection(
            name="chunks",
            metadata={"description": "Document chunk embeddings"}
        )
        
        self.communities_collection = self.client.get_or_create_collection(
            name="communities",
            metadata={"description": "Community summary embeddings"}
        )
    
    def get_stats(self) -> dict:
        """Get vector store statistics."""
        return {
            "entities": self.entities_collection.count() if self.entities_collection else 0,
            "chunks": self.chunks_collection.count() if self.chunks_collection else 0,
            "communities": self.communities_collection.count() if self.communities_collection else 0
        }
    
    def add_entity(self, entity: Entity):
        """Add entity to vector store."""
        if not self.entities_collection:
            return
        
        text_parts = [entity.name]
        if entity.description:
            text_parts.append(entity.description)
        text = " ".join(text_parts)
        
        metadata = {
            "name": entity.name,
            "type": entity.type.value,
            "source_document_id": entity.source_document_id or ""
        }
        
        self.entities_collection.add(
            ids=[entity.id],
            documents=[text],
            metadatas=[metadata]
        )
    
    def add_chunk(self, chunk_id: str, content: str, document_id: str, metadata: dict):
        """Add document chunk to vector store."""
        if not self.chunks_collection:
            return
        
        chunk_metadata = {
            "document_id": document_id,
            **metadata
        }
        
        self.chunks_collection.add(
            ids=[chunk_id],
            documents=[content],
            metadatas=[chunk_metadata]
        )
    
    def add_community(self, community_id: str, summary: str, name: str, level: int):
        """Add community summary to vector store."""
        if not self.communities_collection:
            return
        
        self.communities_collection.add(
            ids=[community_id],
            documents=[summary],
            metadatas={"name": name, "level": level}
        )
    
    def search_entities(self, query: str, entity_type: Optional[str] = None, limit: int = 10) -> List[SearchResult]:
        """Search entities by semantic similarity."""
        if not self.entities_collection:
            return []
        
        where_filter = {"type": entity_type} if entity_type else None
        
        results = self.entities_collection.query(
            query_texts=[query],
            n_results=limit,
            where=where_filter
        )
        
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, entity_id in enumerate(results["ids"][0]):
                search_results.append(SearchResult(
                    id=entity_id,
                    content=results["documents"][0][i],
                    score=1.0 - results["distances"][0][i] if results["distances"] else 0.0,
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {}
                ))
        
        return search_results
    
    def search_chunks(self, query: str, limit: int = 10) -> List[SearchResult]:
        """Search document chunks by semantic similarity."""
        if not self.chunks_collection:
            return []
        
        results = self.chunks_collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                search_results.append(SearchResult(
                    id=chunk_id,
                    content=results["documents"][0][i],
                    score=1.0 - results["distances"][0][i] if results["distances"] else 0.0,
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {}
                ))
        
        return search_results
    
    def search_communities(self, query: str, limit: int = 5) -> List[SearchResult]:
        """Search community summaries by semantic similarity."""
        if not self.communities_collection:
            return []
        
        results = self.communities_collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, community_id in enumerate(results["ids"][0]):
                search_results.append(SearchResult(
                    id=community_id,
                    content=results["documents"][0][i],
                    score=1.0 - results["distances"][0][i] if results["distances"] else 0.0,
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {}
                ))
        
        return search_results
