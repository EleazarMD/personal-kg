# Personal Context Graph (PCG)

**Port:** 8765

**The unified personal intelligence service** combining:
- **PIC (Personal Identity Core)** - Personal identity, preferences, goals, observations
- **KG-API (Knowledge Graph API)** - GraphRAG knowledge graph with entity extraction
- **LIAM (Life Intelligence Augmentation Matrix)** - Personal framework modeling

## Architecture

- **Neo4j** (port 7688) - Graph database for entities, relationships, and PIC data
- **ChromaDB** - Vector embeddings for semantic search
- **AI Gateway** (port 8777) - LLM access for entity extraction and summarization

## Services

### PIC Endpoints (`/api/pic/*`)
- `GET /api/pic/identity` - Get user identity
- `PUT /api/pic/identity` - Update user identity
- `GET /api/pic/preferences` - Get all preferences
- `POST /api/pic/preferences` - Add preference
- `GET /api/pic/goals` - Get all goals
- `POST /api/pic/goals` - Add goal
- `POST /api/pic/learn` - Record observation (for agents)
- `GET /api/pic/context` - Get complete PIC context

### Knowledge Graph Endpoints (`/api/kg/*`)
- `POST /api/kg/ingest` - Ingest document with entity extraction
- `GET /api/kg/entities` - List entities
- `POST /api/kg/entities` - Create entity
- `GET /api/kg/relationships` - Get relationships
- `POST /api/kg/search` - Semantic search
- `POST /api/kg/query` - GraphRAG query (local/global/hybrid)
- `GET /api/kg/graph` - Get graph data for visualization
- `POST /api/kg/communities/build` - Build communities

## Authentication

PIC endpoints require authentication headers:
- `X-PIC-Read-Key` - Read-only access
- `X-PIC-Admin-Key` - Full access (required for writes)

## Setup

```bash
cd services/personal-kg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
./start.sh
```

Or manually:
```bash
source venv/bin/activate
export PYTHONPATH=".:$PYTHONPATH"
python3 -m uvicorn api:app --host 0.0.0.0 --port 8765
```

## Integration

**Nova Agent** - Accesses PIC for identity/preferences/goals
**Hermes Core** - Uses PIC for personalized email responses
**OpenClaw** - Writes observations back to PIC via `/api/pic/learn`
**Dashboard** - Visualizes LIAM dimensions and knowledge graph

## Configuration

See `.env` file for configuration options.
