"""Configuration for Personal Context Graph (PCG)."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Service configuration."""
    
    # Service
    service_name: str = "Personal Context Graph (PCG)"
    service_port: int = 8765
    debug: bool = False
    
    # Neo4j (Knowledge Graph)
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "password")
    
    # ChromaDB (Vector Store)
    chromadb_path: str = os.getenv("CHROMADB_PATH", "./data/chromadb")
    
    # LLM Configuration (via AI Gateway)
    ai_gateway_url: str = os.getenv("AI_GATEWAY_URL", "http://localhost:8777/api/v1")
    ai_gateway_api_key: str = os.getenv("AI_GATEWAY_API_KEY", "ai-gateway-api-key-2024")
    # NIM embeddings service (separate from AI Gateway chat)
    # Runs in Docker on port 8006, model: nvidia/llama-3.2-nv-embedqa-1b-v2
    embedding_endpoint: str = os.getenv("EMBEDDING_ENDPOINT", "http://localhost:8006/v1/embeddings")
    
    # Models
    extraction_model: str = os.getenv("EXTRACTION_MODEL", "claude-haiku-4-5")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nvidia/llama-3.2-nv-embedqa-1b-v2")
    # Hyperspace backend (for push notification webhook)
    hyperspace_url: str = os.getenv("HYPERSPACE_URL", "http://localhost:8031")
    # Consolidation LLM model (for Dreaming pipeline insight summarization)
    consolidation_model: str = os.getenv("CONSOLIDATION_MODEL", "qwen3-30b-a3b")
    summarization_model: str = os.getenv("SUMMARIZATION_MODEL", "claude-haiku-4-5")
    # Embedding vector dimensions. Must match `embedding_model`'s output.
    # nvidia/llama-3.2-nv-embedqa-1b-v2 => 2048, nomic-embed-text => 768. If you change
    # the model after data exists, drop the vector indexes manually before
    # restart (see migrations.py).
    embedding_dims: int = int(os.getenv("EMBEDDING_DIMS", "2048"))
    
    # Chunking
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Community Detection
    min_community_size: int = 5
    
    # PIC Authentication
    pic_read_key: str = os.getenv("PIC_READ_KEY", "pic-read-O-1b4i12nR9gr3kzookTYGZ7NTA_nhSF")
    pic_admin_key: str = os.getenv("PIC_ADMIN_KEY", "pic-admin-nee9oPtIqFwRUaj5vm96A8LEwMkYtUFU")

    # Approval Service (JIT / zero-tolerance) integration.
    # The approval-service issues JIT grants (approved approval_requests) that
    # gate write operations. See pic_auth.py -> _check_jit_grant.
    # NOTE: the approval-service listens on :8407 (the auth-service is :8405).
    approval_service_url: str = os.getenv("APPROVAL_SERVICE_URL", "http://localhost:8407")
    approval_api_key: str = os.getenv("APPROVAL_API_KEY", "ai-gateway-api-key-2024")
    # When True, every write MUST present a valid X-JIT-Grant-Id (full
    # zero-tolerance). When False (default) writes without a grant header are
    # allowed on the strength of the static admin key, but any grant that IS
    # presented must be valid/active. Flip to True to enforce JIT on all writes.
    jit_require_grant: bool = os.getenv("JIT_REQUIRE_GRANT", "false").lower() in ("1", "true", "yes", "on")
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


_settings = None


def get_settings() -> Settings:
    """Get or create settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
