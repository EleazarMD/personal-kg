"""PIC (Personal Identity Core) routes for identity, preferences, and goals."""

from fastapi import APIRouter, HTTPException, Request, Header
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from graph_store import GraphStore
from config import get_settings

# Unified token support
try:
    import sys, os
    _cig_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "cig")
    if os.path.isdir(_cig_path) and _cig_path not in sys.path:
        sys.path.insert(0, _cig_path)
    from unified_token import verify_token as _verify_unified
    _UNIFIED_AVAILABLE = True
except ImportError:
    _UNIFIED_AVAILABLE = False

settings = get_settings()
router = APIRouter(prefix="/api/pic", tags=["PIC"])

# Singleton PIC store instance (default user)
_pic_store = None

# Per-user PIC store registry: user_id → PICStore
_user_pic_stores: Dict[str, "PICStore"] = {}

# Known Neo4j credentials per Bolt URI (same as Nova's bolt.py)
_INSTANCE_CREDENTIALS = {
    "bolt://localhost:7689": ("neo4j", "graphrag2025"),          # hermes-neo4j (Eleazar)
    "bolt://localhost:7720": ("neo4j", "claudia-graph-2026"),    # claudia-neo4j
    "bolt://localhost:7690": ("neo4j", "97abfe8b542de0ba152488da920238b868cb63143e66d036bd95a1663cf3b4bc"),  # kids-neo4j
}

# Default user_id → Bolt URI mapping (fallback when platform DB is unreachable)
_USER_BOLT_URIS = {
    "dfd9379f-a9cd-4241-99e7-140f5e89e3cd": "bolt://localhost:7689",  # Eleazar
    "096f1fe3-16b3-4642-a1d9-2a11bf3843dd": "bolt://localhost:7720",  # Claudia
    "bcb80071-81a4-4e4e-aff2-fe96d6702b08": "bolt://localhost:7690",  # Luca
    "40c2f03c-60b7-4344-b215-dbbcf565f9a5": "bolt://localhost:7690",  # Sofia
}


class PICStore:
    """Store for Personal Identity Core data using Neo4j."""
    
    def __init__(self, graph_store: GraphStore):
        self.graph = graph_store
        self._init_schema()
    
    def _init_schema(self):
        """Initialize PIC schema in Neo4j."""
        with self.graph.driver.session() as session:
            # Create constraints — user_id-scoped for multi-user isolation
            session.run("CREATE CONSTRAINT pic_identity IF NOT EXISTS FOR (i:Identity) REQUIRE (i.user_id, i.id) IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_preference IF NOT EXISTS FOR (p:Preference) REQUIRE p.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_goal IF NOT EXISTS FOR (g:Goal) REQUIRE g.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_observation IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE")
            # Index on user_id for fast filtering
            session.run("CREATE INDEX pic_identity_user IF NOT EXISTS FOR (i:Identity) ON (i.user_id)")
            session.run("CREATE INDEX pic_preference_user IF NOT EXISTS FOR (p:Preference) ON (p.user_id)")
            session.run("CREATE INDEX pic_goal_user IF NOT EXISTS FOR (g:Goal) ON (g.user_id)")
            session.run("CREATE INDEX pic_observation_user IF NOT EXISTS FOR (o:Observation) ON (o.user_id)")
    
    def get_identity(self, user_id: str = "") -> Optional[Dict[str, Any]]:
        """Get user identity. Parses JSON-stringified nested fields back to dicts/lists."""
        import json as _json
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("MATCH (i:Identity {user_id: $user_id}) RETURN i LIMIT 1", user_id=user_id)
            else:
                result = session.run("MATCH (i:Identity) RETURN i LIMIT 1")
            record = result.single()
            if not record:
                return None
            data = dict(record["i"])
            # Deserialize fields that upsert_identity flattened to JSON
            for k, v in list(data.items()):
                if isinstance(v, str) and v and v[0] in "{[":
                    try:
                        data[k] = _json.loads(v)
                    except (ValueError, TypeError):
                        pass  # leave as string if not valid JSON
            return data
    
    def upsert_identity(self, identity_data: Dict[str, Any], user_id: str = "") -> Dict[str, Any]:
        """Create or update user identity.

        Neo4j node properties must be primitives or flat lists of primitives.
        Any nested dict/list-of-dict values (e.g. ``metadata``, ``education``)
        are JSON-serialized before being stored and parsed on read.
        """
        import json as _json

        # Flatten: serialize any dict/list-of-dict values to JSON strings
        flat: Dict[str, Any] = {}
        for k, v in identity_data.items():
            if isinstance(v, dict):
                flat[k] = _json.dumps(v)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                flat[k] = _json.dumps(v)
            else:
                flat[k] = v

        # Add user_id for multi-user isolation
        if user_id:
            flat["user_id"] = user_id

        with self.graph.driver.session() as session:
            if user_id:
                session.run(
                    """
                    MERGE (i:Identity {user_id: $user_id, id: 'user_identity'})
                    ON CREATE SET i.created_at = datetime()
                    SET i += $data,
                        i.updated_at = datetime()
                    """,
                    data=flat, user_id=user_id,
                )
            else:
                # No user_id provided -- create with a placeholder user_id to
                # satisfy the composite uniqueness constraint. This path should
                # not be hit in production (all callers must pass user_id).
                session.run(
                    """
                    MERGE (i:Identity {user_id: 'unknown', id: 'user_identity'})
                    ON CREATE SET i.created_at = datetime()
                    SET i += $data,
                        i.updated_at = datetime()
                    """,
                    data=flat,
                )
            return self.get_identity(user_id)
    
    def get_preferences(self, user_id: str = "") -> List[Dict[str, Any]]:
        """Get all preferences."""
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("""
                    MATCH (p:Preference {user_id: $user_id})
                    RETURN p
                    ORDER BY p.created_at DESC
                """, user_id=user_id)
            else:
                result = session.run("""
                    MATCH (p:Preference)
                    RETURN p
                    ORDER BY p.created_at DESC
                """)
            return [dict(record["p"]) for record in result]
    
    def add_preference(self, category: str, key: str, value: Any, context: Optional[str] = None, user_id: str = "") -> Dict[str, Any]:
        """Add a preference."""
        import uuid
        pref_id = f"pref_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (p:Preference {
                    id: $id,
                    user_id: $user_id,
                    category: $category,
                    key: $key,
                    value: $value,
                    context: $context,
                    created_at: datetime()
                })
            """, id=pref_id, user_id=user_id, category=category, key=key, value=value, context=context)
            
            result = session.run("MATCH (p:Preference {id: $id}) RETURN p", id=pref_id)
            record = result.single()
            return dict(record["p"]) if record else {}
    
    def get_goals(self, user_id: str = "") -> List[Dict[str, Any]]:
        """Get all goals."""
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("""
                    MATCH (g:Goal {user_id: $user_id})
                    RETURN g
                    ORDER BY g.priority DESC, g.created_at DESC
                """, user_id=user_id)
            else:
                result = session.run("""
                    MATCH (g:Goal)
                    RETURN g
                    ORDER BY g.priority DESC, g.created_at DESC
                """)
            return [dict(record["g"]) for record in result]
    
    def add_goal(self, title: str, description: Optional[str] = None, priority: int = 5, deadline: Optional[str] = None, user_id: str = "") -> Dict[str, Any]:
        """Add a goal."""
        import uuid
        goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (g:Goal {
                    id: $id,
                    user_id: $user_id,
                    title: $title,
                    description: $description,
                    priority: $priority,
                    deadline: $deadline,
                    status: 'active',
                    created_at: datetime()
                })
            """, id=goal_id, user_id=user_id, title=title, description=description, priority=priority, deadline=deadline)
            
            result = session.run("MATCH (g:Goal {id: $id}) RETURN g", id=goal_id)
            record = result.single()
            return dict(record["g"]) if record else {}
    
    def record_observation(self, observation: str, category: Optional[str] = None, source: Optional[str] = None, user_id: str = "") -> Dict[str, Any]:
        """Record an observation about the user."""
        import uuid
        obs_id = f"obs_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (o:Observation {
                    id: $id,
                    user_id: $user_id,
                    observation: $observation,
                    category: $category,
                    source: $source,
                    created_at: datetime()
                })
            """, id=obs_id, user_id=user_id, observation=observation, category=category, source=source)
            
            result = session.run("MATCH (o:Observation {id: $id}) RETURN o", id=obs_id)
            record = result.single()
            return dict(record["o"]) if record else {}
    
    def get_pic_stats(self, user_id: str = "") -> Dict[str, int]:
        """Get PIC statistics."""
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("""
                    MATCH (p:Preference {user_id: $user_id})
                    WITH count(p) as preferences
                    MATCH (g:Goal {user_id: $user_id})
                    WITH preferences, count(g) as goals
                    MATCH (o:Observation {user_id: $user_id})
                    RETURN preferences, goals, count(o) as observations
                """, user_id=user_id)
            else:
                result = session.run("""
                    MATCH (p:Preference)
                    WITH count(p) as preferences
                    MATCH (g:Goal)
                    WITH preferences, count(g) as goals
                    MATCH (o:Observation)
                    RETURN preferences, goals, count(o) as observations
                """)
            record = result.single()
            if record:
                return {
                    "preferences": record["preferences"],
                    "goals": record["goals"],
                    "observations": record["observations"]
                }
            return {"preferences": 0, "goals": 0, "observations": 0}


def get_pic_store(user_id: str = "") -> PICStore:
    """Get or create PIC store. Per-user only — no global fallback.

    JIT zero-tolerance: empty user_id raises ValueError instead of
    silently using Eleazar's default Neo4j instance.
    """
    if not user_id:
        raise ValueError("user_id is required — no global fallback")
    
    # Per-user store
    if user_id in _user_pic_stores:
        return _user_pic_stores[user_id]
    
    # Resolve user's Neo4j credentials from Infisical (with fallback)
    uri, neo4j_user, neo4j_password = _resolve_user_neo4j_credentials(user_id)
    if not uri:
        raise ValueError(f"No Neo4j URI configured for user {user_id}")
    
    graph = GraphStore(neo4j_uri=uri, neo4j_user=neo4j_user, neo4j_password=neo4j_password)
    graph.connect()
    store = PICStore(graph)
    _user_pic_stores[user_id] = store
    return store


def _resolve_user_neo4j_uri(user_id: str) -> Optional[str]:
    """Resolve a user_id to their PCG Neo4j Bolt URI.

    Postgres (user_kg_config) is the source of truth. Falls back to the
    static map for dev/offline use. Returns None if the user has no
    configured graph.
    """
    # Primary: platform DB lookup (psycopg2 — sync, available in the venv
    # via the system path that _make_jti_checker also relies on).
    try:
        import psycopg2
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            conn = psycopg2.connect(db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pcg_bolt_uri FROM user_kg_config WHERE user_id = %s",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0]
            finally:
                conn.close()
    except Exception:
        pass

    # Fallback: static hardcoded map (dev / DB unreachable)
    return _USER_BOLT_URIS.get(user_id)


def _resolve_user_neo4j_credentials(user_id: str) -> tuple:
    """Resolve a user_id to (uri, user, password) for their Neo4j instance.

    Postgres (user_kg_config) is the source of truth for the URI.
    Credentials come from _INSTANCE_CREDENTIALS keyed by URI.
    Returns (None, None, None) if resolution fails.
    """
    uri = _resolve_user_neo4j_uri(user_id)
    if not uri:
        return (None, None, None)
    user, password = _INSTANCE_CREDENTIALS.get(uri, (settings.neo4j_user, settings.neo4j_password))
    return (uri, user, password)


def _make_jti_checker():
    """Create a JTI revocation checker querying revoked_tokens table.
    Fails open on DB errors — dashboard-side verifyTokenWithRevocation is primary.
    """
    try:
        import psycopg2
        _dsn = os.environ.get("DATABASE_URL", "postgresql://eleazar@localhost/ecosystem_unified")

        def _check(jti: str) -> bool:
            try:
                conn = psycopg2.connect(_dsn)
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM revoked_tokens WHERE jti = %s LIMIT 1", (jti,))
                        return cur.fetchone() is not None
                finally:
                    conn.close()
            except Exception:
                return False

        return _check
    except ImportError:
        return None


def _resolve_user_id(x_user_id: str, authorization: str = "") -> str:
    """Resolve user_id from X-User-Id header or unified JWT Bearer token."""
    # Try unified JWT first
    if _UNIFIED_AVAILABLE and authorization:
        token = authorization.replace("Bearer ", "", 1) if authorization.startswith("Bearer ") else authorization
        nextauth_secret = os.environ.get("NEXTAUTH_SECRET", "your-development-secret-change-in-production")
        # P2.9: Check JTI revocation deny-list
        revocation_checker = _make_jti_checker()
        ctx = _verify_unified(token, secret=nextauth_secret, revocation_checker=revocation_checker)
        if ctx:
            return ctx.userId
    # Fall back to X-User-Id header
    return x_user_id or ""


# Request/Response Models
class IdentityUpdate(BaseModel):
    name: Optional[str] = None
    preferred_name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    roles: Optional[List[str]] = None
    organization: Optional[str] = None
    communication_style: Optional[str] = None
    cognitive_style: Optional[str] = None
    technical_level: Optional[str] = None
    preferred_response_length: Optional[str] = None
    demographics: Optional[Dict[str, Any]] = None
    communication_preferences: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class PreferenceCreate(BaseModel):
    category: str
    key: str
    value: Any
    context: Optional[str] = None


class GoalCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 5
    deadline: Optional[str] = None


class ObservationCreate(BaseModel):
    observation: str
    category: Optional[str] = None
    source: Optional[str] = None


# Routes
@router.get("/identity")
async def get_identity(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get user identity."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    identity = store.get_identity(user_id=uid)
    if not identity:
        return {"message": "No identity configured"}
    return identity


@router.put("/identity")
async def update_identity(
    data: IdentityUpdate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Update user identity."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    identity_data = data.model_dump(exclude_none=True)
    updated = store.upsert_identity(identity_data, user_id=uid)
    return {"status": "updated", "identity": updated}


@router.get("/preferences")
async def get_preferences(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get all preferences."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    preferences = store.get_preferences(user_id=uid)
    return {"preferences": preferences, "count": len(preferences)}


@router.post("/preferences")
async def add_preference(
    data: PreferenceCreate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Add a preference."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    preference = store.add_preference(
        category=data.category,
        key=data.key,
        value=data.value,
        context=data.context,
        user_id=uid,
    )
    return {"status": "created", "preference": preference}


@router.get("/goals")
async def get_goals(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get all goals."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    goals = store.get_goals(user_id=uid)
    return {"goals": goals, "count": len(goals)}


@router.post("/goals")
async def add_goal(
    data: GoalCreate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Add a goal."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    goal = store.add_goal(
        title=data.title,
        description=data.description,
        priority=data.priority,
        deadline=data.deadline,
        user_id=uid,
    )
    return {"status": "created", "goal": goal}


@router.post("/learn")
async def record_observation(
    data: ObservationCreate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Record an observation (learning endpoint for agents)."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    observation = store.record_observation(
        observation=data.observation,
        category=data.category,
        source=data.source,
        user_id=uid,
    )
    return {"status": "recorded", "observation": observation}


@router.get("/context")
async def get_full_context(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get complete PIC context for agent initialization."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    
    identity = store.get_identity(user_id=uid)
    preferences = store.get_preferences(user_id=uid)
    goals = store.get_goals(user_id=uid)
    
    return {
        "identity": identity,
        "preferences": preferences,
        "goals": goals,
        "timestamp": datetime.utcnow().isoformat()
    }
