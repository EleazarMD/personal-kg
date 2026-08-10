"""PIC (Personal Identity Core) routes for identity, preferences, and goals."""
import logging
logger = logging.getLogger(__name__)

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
    "bolt://localhost:7690": ("neo4j", "kids-pcg-dev-password"),  # kids-neo4j
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
            # Tiered preference labels — profile dedup by (user_id, category, key)
            session.run("CREATE CONSTRAINT pic_profile_pref IF NOT EXISTS FOR (p:ProfilePreference) REQUIRE (p.user_id, p.category, p.key) IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_event_pref IF NOT EXISTS FOR (p:EventPreference) REQUIRE p.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_system_pref IF NOT EXISTS FOR (p:SystemPreference) REQUIRE p.id IS UNIQUE")
            # Index on user_id for fast filtering
            session.run("CREATE INDEX pic_identity_user IF NOT EXISTS FOR (i:Identity) ON (i.user_id)")
            session.run("CREATE INDEX pic_preference_user IF NOT EXISTS FOR (p:Preference) ON (p.user_id)")
            session.run("CREATE INDEX pic_goal_user IF NOT EXISTS FOR (g:Goal) ON (g.user_id)")
            session.run("CREATE INDEX pic_observation_user IF NOT EXISTS FOR (o:Observation) ON (o.user_id)")
            session.run("CREATE INDEX pic_profile_pref_user IF NOT EXISTS FOR (p:ProfilePreference) ON (p.user_id)")
            session.run("CREATE INDEX pic_event_pref_user IF NOT EXISTS FOR (p:EventPreference) ON (p.user_id)")
            session.run("CREATE INDEX pic_system_pref_user IF NOT EXISTS FOR (p:SystemPreference) ON (p.user_id)")
            # HealthProfile — one per user (MERGE on user_id)
            session.run("CREATE CONSTRAINT pic_health_profile IF NOT EXISTS FOR (h:HealthProfile) REQUIRE h.user_id IS UNIQUE")
    
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
    
    def get_health_profile(self, user_id: str = "") -> Optional[Dict[str, Any]]:
        """Get structured health profile for a user."""
        with self.graph.driver.session() as session:
            result = session.run(
                "MATCH (h:HealthProfile {user_id: $user_id}) RETURN h LIMIT 1",
                user_id=user_id,
            )
            record = result.single()
            if not record:
                return None
            data = dict(record["h"])
            # Remove internal fields from response
            data.pop("id", None)
            return data

    def upsert_health_profile(self, updates: Dict[str, Any], user_id: str = "") -> Dict[str, Any]:
        """Create or update structured health profile. Only sets provided fields."""
        import uuid
        # Filter out None values so we don't overwrite existing fields with null
        clean = {k: v for k, v in updates.items() if v is not None}
        if not clean:
            return self.get_health_profile(user_id) or {"user_id": user_id, "message": "No fields to update"}

    def record_weigh_in(self, weight_kg: float, user_id: str = "", notes: str = "") -> Dict[str, Any]:
        """Record a weigh-in event and sync latest weight to HealthProfile."""
        import uuid
        from datetime import datetime
        ts = datetime.utcnow().isoformat()
        event_id = f"weigh_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            # Store as EventPreference for time-series tracking
            session.run("""
                CREATE (p:EventPreference {
                    id: $id,
                    user_id: $user_id,
                    category: 'health',
                    key: $key,
                    value: $weight,
                    notes: $notes,
                    created_at: datetime()
                })
            """, id=event_id, user_id=user_id, key=f"weigh_in_{ts}", weight=str(weight_kg), notes=notes)
            
            # Sync to HealthProfile
            session.run("""
                MERGE (h:HealthProfile {user_id: $user_id})
                SET h.weight_kg = $weight, h.updated_at = datetime()
            """, user_id=user_id, weight=weight_kg)
            
            return {"id": event_id, "weight_kg": weight_kg, "timestamp": ts, "synced_to_profile": True}

    def get_weigh_ins(self, user_id: str = "", limit: int = 30) -> list[Dict[str, Any]]:
        """Get weigh-in history, most recent first."""
        with self.graph.driver.session() as session:
            result = session.run("""
                MATCH (p:EventPreference)
                WHERE p.user_id = $user_id AND p.category = 'health' AND p.key STARTS WITH 'weigh_in_'
                RETURN p
                ORDER BY p.created_at DESC
                LIMIT $limit
            """, user_id=user_id, limit=limit)
            return [dict(r["p"]) for r in result]

        with self.graph.driver.session() as session:
            session.run("""
                MERGE (h:HealthProfile {user_id: $user_id})
                ON CREATE SET h.id = $id, h.created_at = datetime()
                SET h += $data,
                    h.updated_at = datetime()
            """, user_id=user_id, id=f"hp_{uuid.uuid4().hex[:12]}", data=clean)

            result = session.run(
                "MATCH (h:HealthProfile {user_id: $user_id}) RETURN h LIMIT 1",
                user_id=user_id,
            )
            record = result.single()
            data = dict(record["h"]) if record else {}
            data.pop("id", None)
            return data

    def get_preferences(self, user_id: str = "", tier: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all preferences. Optional tier filter: 'profile', 'event', 'system'.

        Reads from tiered labels (ProfilePreference, EventPreference, SystemPreference)
        AND legacy :Preference nodes (untiered data from before migration).
        """
        with self.graph.driver.session() as session:
            if tier:
                # Map tier to label
                label = {"profile": "ProfilePreference", "event": "EventPreference", "system": "SystemPreference"}.get(tier)
                if not label:
                    raise ValueError(f"Invalid tier: {tier}")
                if user_id:
                    result = session.run(f"""
                        MATCH (p:{label} {{user_id: $user_id}})
                        RETURN p
                        ORDER BY p.created_at DESC
                    """, user_id=user_id)
                else:
                    result = session.run(f"""
                        MATCH (p:{label})
                        RETURN p
                        ORDER BY p.created_at DESC
                    """)
            else:
                # Return all: tiered labels + legacy :Preference
                if user_id:
                    result = session.run("""
                        MATCH (p)
                        WHERE (p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference)
                          AND p.user_id = $user_id
                        RETURN p
                        ORDER BY p.created_at DESC
                    """, user_id=user_id)
                else:
                    result = session.run("""
                        MATCH (p)
                        WHERE p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference
                        RETURN p
                        ORDER BY p.created_at DESC
                    """)
            return [dict(record["p"]) for record in result]
    
    def add_preference(self, category: str, key: str, value: Any, context: Optional[str] = None, user_id: str = "", tier: str = "profile", source: str = "explicit", ttl_days: Optional[int] = None) -> Dict[str, Any]:
        """Add or update a preference. Tier determines write pattern:

        - profile: MERGE on (user_id, category, key) — dedup/upsert
        - event:   CREATE — allow multiple, optional TTL
        - system:  CREATE — allow multiple, optional TTL
        """
        import uuid
        pref_id = f"pref_{uuid.uuid4().hex[:12]}"
        label = {"profile": "ProfilePreference", "event": "EventPreference", "system": "SystemPreference"}.get(tier, "ProfilePreference")

        # Compute expiration timestamp if TTL provided
        expires_at = None
        if ttl_days:
            from datetime import datetime, timedelta
            expires_at = (datetime.utcnow() + timedelta(days=ttl_days)).isoformat()

        with self.graph.driver.session() as session:
            if tier == "profile":
                # MERGE: upsert by (user_id, category, key) — dedup
                session.run(f"""
                    MERGE (p:{label} {{user_id: $user_id, category: $category, key: $key}})
                    ON CREATE SET p.id = $id, p.created_at = datetime()
                    SET p.value = $value,
                        p.context = $context,
                        p.source = $source,
                        p.tier = $tier,
                        p.updated_at = datetime()
                """, id=pref_id, user_id=user_id, category=category, key=key,
                    value=value, context=context, source=source, tier=tier)
            else:
                # CREATE: always new node (events/system are append-only)
                session.run(f"""
                    CREATE (p:{label} {{
                        id: $id,
                        user_id: $user_id,
                        category: $category,
                        key: $key,
                        value: $value,
                        context: $context,
                        source: $source,
                        tier: $tier,
                        created_at: datetime(),
                        expires_at: $expires_at
                    }})
                """, id=pref_id, user_id=user_id, category=category, key=key,
                    value=value, context=context, source=source, tier=tier, expires_at=expires_at)

            result = session.run(f"MATCH (p:{label} {{id: $id}}) RETURN p", id=pref_id)
            record = result.single()
            if not record:
                # For MERGE, the id may not match if it upserted an existing node
                result = session.run(f"""
                    MATCH (p:{label} {{user_id: $user_id, category: $category, key: $key}})
                    RETURN p
                """, user_id=user_id, category=category, key=key)
                record = result.single()
            return dict(record["p"]) if record else {}

    def delete_preference(self, pref_id: str, user_id: str = "") -> bool:
        """Delete a preference by id. Verifies ownership via user_id."""
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("""
                    MATCH (p)
                    WHERE (p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference)
                      AND p.id = $id AND p.user_id = $user_id
                    DETACH DELETE p
                    RETURN count(p) as deleted
                """, id=pref_id, user_id=user_id)
            else:
                result = session.run("""
                    MATCH (p)
                    WHERE (p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference)
                      AND p.id = $id
                    DETACH DELETE p
                    RETURN count(p) as deleted
                """, id=pref_id)
            record = result.single()
            return record and record["deleted"] > 0

    def update_preference(self, pref_id: str, user_id: str = "", value: Any = None, context: Optional[str] = None, tier: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update a preference by id. Verifies ownership via user_id."""
        set_clauses = []
        params = {"id": pref_id, "user_id": user_id}
        if value is not None:
            set_clauses.append("p.value = $value")
            params["value"] = value
        if context is not None:
            set_clauses.append("p.context = $context")
            params["context"] = context
        if not set_clauses:
            return None
        set_clauses.append("p.updated_at = datetime()")
        set_stmt = ", ".join(set_clauses)

        with self.graph.driver.session() as session:
            result = session.run(f"""
                MATCH (p)
                WHERE (p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference)
                  AND p.id = $id AND p.user_id = $user_id
                SET {set_stmt}
                RETURN p
            """, **params)
            record = result.single()
            return dict(record["p"]) if record else None
    
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
        """Record an observation about the user.
        If the observation text is a JSON blob, extracts the 'value' field
        as the human-readable text and stores the full JSON as metadata."""
        import uuid, json as _json
        obs_id = f"obs_{uuid.uuid4().hex[:12]}"

        # Parse JSON observations — extract clean text
        metadata = None
        clean_text = observation
        if isinstance(observation, str) and observation.strip().startswith("{"):
            try:
                parsed = _json.loads(observation)
                clean_text = parsed.get("value", parsed.get("observation", parsed.get("text", observation)))
                if not category:
                    category = parsed.get("category", category)
                if not source:
                    source = parsed.get("source", source)
                metadata = observation  # keep full JSON as metadata
            except (ValueError, TypeError):
                pass

        with self.graph.driver.session() as session:
            # Check for existing preference with similar text (reinforcement)
            if user_id and clean_text and len(clean_text) > 10:
                try:
                    # Simple text match — check if any preference contains the key phrase
                    key_phrase = clean_text[:50].lower()
                    result = session.run(
                        """
                        MATCH (p:ProfilePreference {user_id: $user_id})
                        WHERE toLower(p.value) CONTAINS $phrase
                        SET p.last_reinforced_at = datetime(), p.confidence = coalesce(p.confidence, 0.5) + 0.1
                        RETURN count(p) as reinforced
                        """,
                        user_id=user_id,
                        phrase=key_phrase,
                    )
                    reinforced = result.single()["reinforced"]
                    if reinforced > 0:
                        # Existing preference found and reinforced — still create the
                        # observation for audit trail, but mark it as reinforced
                        pass
                except Exception:
                    pass

            session.run("""
                CREATE (o:Observation {
                    id: $id,
                    user_id: $user_id,
                    observation: $observation,
                    category: $category,
                    source: $source,
                    status: 'new',
                    metadata: $metadata,
                    created_at: datetime()
                })
            """, id=obs_id, user_id=user_id, observation=clean_text, category=category, source=source, metadata=metadata)
            
            result = session.run("MATCH (o:Observation {id: $id}) RETURN o", id=obs_id)
            record = result.single()
            return dict(record["o"]) if record else {}
    
    def get_pic_stats(self, user_id: str = "") -> Dict[str, int]:
        """Get PIC statistics."""
        with self.graph.driver.session() as session:
            if user_id:
                result = session.run("""
                    MATCH (p)
                    WHERE (p:ProfilePreference OR p:EventPreference OR p:SystemPreference OR p:Preference)
                      AND p.user_id = $user_id
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
    Credentials come from _INSTANCE_CREDENTIALS keyed by URI — this is a
    BOOTSTRAP-ONLY fallback logged at CRITICAL level. In production,
    Infisical should be the credential source.
    Returns (None, None, None) if resolution fails.
    """
    uri = _resolve_user_neo4j_uri(user_id)
    if not uri:
        return (None, None, None)
    user, password = _INSTANCE_CREDENTIALS.get(uri, (settings.neo4j_user, settings.neo4j_password))
    logger.critical(
        f"SECURITY: Using static fallback credentials for user {user_id[:8]} "
        f"at {uri} — Infisical unreachable. These are plaintext passwords "
        f"in source code and must not be relied upon in production."
    )
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
    tier: str = "profile"  # "profile" | "event" | "system"
    source: str = "explicit"  # "explicit" | "observed" | "consolidated"
    ttl_days: Optional[int] = None  # auto-expire for event/system tiers


class PreferenceUpdate(BaseModel):
    value: Optional[Any] = None
    context: Optional[str] = None
    tier: Optional[str] = None


class HealthProfileUpdate(BaseModel):
    """Structured health profile with typed fields for wellness/nutrition/fitness."""
    # Anthropometric
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    body_fat_pct: Optional[float] = None
    lean_mass_kg: Optional[float] = None
    waist_cm: Optional[float] = None
    hip_cm: Optional[float] = None
    neck_cm: Optional[float] = None
    chest_cm: Optional[float] = None
    frame_size: Optional[str] = None  # "small" | "medium" | "large"

    # Demographics
    age: Optional[int] = None
    sex: Optional[str] = None  # "male" | "female"

    # Metabolic / Lab markers
    resting_hr: Optional[int] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    hba1c: Optional[float] = None
    fasting_glucose_mgdl: Optional[int] = None
    ldl_mgdl: Optional[int] = None
    hdl_mgdl: Optional[int] = None
    triglycerides_mgdl: Optional[int] = None
    tdee_kcal: Optional[int] = None
    bmr_kcal: Optional[int] = None

    # Nutrition targets
    caloric_target_kcal: Optional[int] = None
    protein_target_g: Optional[int] = None
    carbs_target_g: Optional[int] = None
    fat_target_g: Optional[int] = None
    hydration_oz: Optional[float] = None
    caffeine_mg_ceiling: Optional[int] = None
    meal_timing_window: Optional[str] = None  # e.g. "12:00-20:00" for IF
    meal_count: Optional[int] = None

    # Physical activity
    activity_level: Optional[str] = None  # "sedentary"|"light"|"moderate"|"active"|"very_active"
    training_split: Optional[str] = None  # free text description
    strength_days_per_week: Optional[int] = None
    cardio_days_per_week: Optional[int] = None
    step_goal: Optional[int] = None
    max_hr: Optional[int] = None
    zone2_hr_target: Optional[int] = None
    sleep_target_hours: Optional[float] = None

    # GLP-1 / Ozempic tracking
    glp1_active: Optional[bool] = None
    glp1_medication: Optional[str] = None
    glp1_dose: Optional[str] = None
    glp1_injection_day: Optional[str] = None  # e.g. "Sunday"

    # Fitness goal
    fitness_goal: Optional[str] = None  # "cut" | "maintain" | "bulk" | "recomp"
    target_weight_kg: Optional[float] = None
    target_body_fat_pct: Optional[float] = None


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
    tier: Optional[str] = None,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get all preferences. Optional tier filter: 'profile', 'event', 'system'."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    preferences = store.get_preferences(user_id=uid, tier=tier)
    return {"preferences": preferences, "count": len(preferences)}


@router.post("/preferences")
async def add_preference(
    data: PreferenceCreate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Add or update a preference. Tier determines write pattern."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    preference = store.add_preference(
        category=data.category,
        key=data.key,
        value=data.value,
        context=data.context,
        user_id=uid,
        tier=data.tier,
        source=data.source,
        ttl_days=data.ttl_days,
    )
    if data.tier == "profile":
        return {"status": "upserted", "preference": preference}
    return {"status": "created", "preference": preference}

@router.delete("/preferences/{pref_id}")
async def delete_preference(
    pref_id: str,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Delete a preference by id."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    deleted = store.delete_preference(pref_id=pref_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preference not found")
    return {"status": "deleted", "id": pref_id}

@router.patch("/preferences/{pref_id}")
async def update_preference(
    pref_id: str,
    data: PreferenceUpdate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Update a preference by id."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    updated = store.update_preference(
        pref_id=pref_id,
        user_id=uid,
        value=data.value,
        context=data.context,
        tier=data.tier,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Preference not found")
    return {"status": "updated", "preference": updated}


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
    health_profile = store.get_health_profile(user_id=uid)

    return {
        "identity": identity,
        "preferences": preferences,
        "goals": goals,
        "health_profile": health_profile,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/observations")
async def list_observations(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """List observations for the signed-in user. Optional status/category filters."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    with store.graph.driver.session() as session:
        if status and category:
            result = session.run(
                "MATCH (o:Observation {user_id: $user_id}) WHERE o.status = $status AND o.category = $category RETURN o ORDER BY o.created_at DESC LIMIT $limit",
                user_id=uid, status=status, category=category, limit=limit,
            )
        elif status:
            result = session.run(
                "MATCH (o:Observation {user_id: $user_id}) WHERE o.status = $status RETURN o ORDER BY o.created_at DESC LIMIT $limit",
                user_id=uid, status=status, limit=limit,
            )
        elif category:
            result = session.run(
                "MATCH (o:Observation {user_id: $user_id}) WHERE o.category = $category RETURN o ORDER BY o.created_at DESC LIMIT $limit",
                user_id=uid, category=category, limit=limit,
            )
        else:
            result = session.run(
                "MATCH (o:Observation {user_id: $user_id}) RETURN o ORDER BY o.created_at DESC LIMIT $limit",
                user_id=uid, limit=limit,
            )
        observations = []
        for r in result:
            obs = dict(r["o"])
            # Serialize Neo4j DateTime
            for k, v in list(obs.items()):
                if hasattr(v, "year") and hasattr(v, "month"):
                    obs[k] = str(v)
            observations.append(obs)
    return {"observations": observations, "count": len(observations)}


@router.get("/health-profile")
async def get_health_profile(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get structured health profile for the user."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    profile = store.get_health_profile(user_id=uid)
    if not profile:
        return {"message": "No health profile configured", "health_profile": None}
    return {"health_profile": profile}


@router.put("/health-profile")
async def update_health_profile(
    data: HealthProfileUpdate,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Create or update structured health profile. Only provided fields are set."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)
    updates = data.model_dump(exclude_none=True)
    profile = store.upsert_health_profile(updates, user_id=uid)
    return {"status": "updated", "health_profile": profile}

@router.post("/weigh-in")
async def record_weigh_in(
    weight_kg: float,
    notes: str = "",
    user_id: str = "",
    x_pic_admin_key: str = Header(None, alias="X-PIC-Admin-Key"),
):
    """Record a weigh-in and sync to health profile."""
    uid = user_id or _resolve_user_from_key(x_pic_admin_key)
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")
    store = get_pic_store(uid)
    result = store.record_weigh_in(weight_kg=weight_kg, user_id=uid, notes=notes)
    return result

@router.get("/weigh-ins")
async def get_weigh_ins(
    user_id: str = "",
    limit: int = 30,
    x_pic_read_key: str = Header(None, alias="X-PIC-Read-Key"),
):
    """Get weigh-in history."""
    uid = user_id or _resolve_user_from_key(x_pic_read_key)
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")
    store = get_pic_store(uid)
    return {"weigh_ins": store.get_weigh_ins(user_id=uid, limit=limit)}


# =========================================================================
# Family-level preferences (shared across users in the same family)
# =========================================================================
# Some preferences apply to the whole family (e.g., "always order avocado
# on the side"). These are stored as :FamilyPreference nodes with a
# family_id that links related users. Each user inherits their family
# preferences unless they have a personal override.


# =========================================================================
# Family-level preferences (shared across users in the same family)
# =========================================================================

@router.get("/family-preferences")
async def get_family_preferences(
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Get family-level preferences that apply to the signed-in user."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)

    _FAMILY_MAP = {
        "dfd9379f-a9cd-4241-99e7-140f5e89e3cd": "flores_family",
        "096f1fe3-16b3-4642-a1d9-2a11bf3843dd": "flores_family",
        "bcb80071-81a4-4e4e-aff2-fe96d6702b08": "flores_family",
        "40c2f03c-60b7-4344-b215-dbbcf565f9a5": "flores_family",
    }
    family_id = _FAMILY_MAP.get(uid, "")
    if not family_id:
        return {"family_preferences": [], "count": 0}

    with store.graph.driver.session() as session:
        result = session.run(
            "MATCH (p:FamilyPreference {family_id: $family_id}) RETURN p ORDER BY p.created_at DESC",
            family_id=family_id,
        )
        prefs = []
        for r in result:
            d = dict(r["p"])
            for k, v in list(d.items()):
                if hasattr(v, "year") and hasattr(v, "month"):
                    d[k] = str(v)
            prefs.append(d)
    return {"family_preferences": prefs, "count": len(prefs), "family_id": family_id}


@router.post("/family-preferences")
async def add_family_preference(
    data: dict,
    x_user_id: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Add a family-level preference."""
    uid = _resolve_user_id(x_user_id, authorization)
    store = get_pic_store(uid)

    _FAMILY_MAP = {
        "dfd9379f-a9cd-4241-99e7-140f5e89e3cd": "flores_family",
        "096f1fe3-16b3-4642-a1d9-2a11bf3843dd": "flores_family",
        "bcb80071-81a4-4e4e-aff2-fe96d6702b08": "flores_family",
        "40c2f03c-60b7-4344-b215-dbbcf565f9a5": "flores_family",
    }
    family_id = _FAMILY_MAP.get(uid, "")
    if not family_id:
        raise HTTPException(status_code=400, detail="User not in a family group")

    import uuid as _uuid
    pref_id = f"fampref_{_uuid.uuid4().hex[:12]}"
    with store.graph.driver.session() as session:
        session.run(
            """
            CREATE (p:FamilyPreference {
                id: $id,
                family_id: $family_id,
                category: $category,
                key: $key,
                value: $value,
                source: $source,
                created_by: $user_id,
                created_at: datetime()
            })
            """,
            id=pref_id,
            family_id=family_id,
            category=data.get("category", "general"),
            key=data.get("key", pref_id),
            value=data.get("value", ""),
            source=data.get("source", "explicit"),
            user_id=uid,
        )
    return {"status": "created", "id": pref_id, "family_id": family_id}
