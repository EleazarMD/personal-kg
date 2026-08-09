# Neo4j Vector Migration Plan — ChromaDB → Native Vector Indexes

## Status: Deferred (requires careful migration + testing)

## Current state
- ChromaDB stores entity/chunk/community embeddings in 3 collections
- Neo4j 5 native vector indexes already created (migrations.py):
  - preference_embedding (Preference, 768-dim cosine)
  - observation_embedding (Observation, 768-dim cosine)
  - communication_style_embedding (CommunicationStyle, 768-dim cosine)
  - insight_embedding (Insight, 768-dim cosine)
- PCG-owned nodes (Preference, Observation, etc.) already get embeddings
  via embeddings.py → AI Gateway
- Entity/Document/Community nodes still use ChromaDB for vector search

## Migration steps
1. Add vector indexes for Entity, Document, Community labels in migrations.py
2. Backfill embeddings from ChromaDB → Neo4j (one-time script)
3. Add Neo4j vector search methods to graph_store.py:
   - `vector_search_entities(query_embedding, limit)` — uses db.index.vector.queryNodes
   - `vector_search_chunks(query_embedding, limit)`
   - `vector_search_communities(query_embedding, limit)`
4. Update api.py search endpoints to use graph_store vector search
   instead of vector_store (ChromaDB)
5. Keep ChromaDB as fallback during transition (dual-write period)
6. Remove ChromaDB dependency after verification

## Cypher for Neo4j vector search
```cypher
CALL db.index.vector.queryNodes("entity_embedding", $limit, $query_embedding)
YIELD node, score
RETURN node, score
ORDER BY score DESC
```

## Risk
- ChromaDB uses sentence-transformers locally; Neo4j vectors use AI Gateway
  (nomic-embed-text). Dimension mismatch if not careful (768 vs 1536).
- Must re-embed all entities during migration if model changed.
- Search result ordering may differ between cosine implementations.
