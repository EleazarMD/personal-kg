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
    
    # Models
    extraction_model: str = "claude-haiku-4-5"
    embedding_model: str = "text-embedding-3-small"
    summarization_model: str = "claude-haiku-4-5"
    
    # Chunking
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Community Detection
    min_community_size: int = 5
    
    # PIC Authentication
    pic_read_key: str = os.getenv("PIC_READ_KEY", "dev-read-key-change-in-prod")
    pic_admin_key: str = os.getenv("PIC_ADMIN_KEY", "dev-admin-key-change-in-prod")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


_settings = None


def get_settings() -> Settings:
    """Get or create settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
