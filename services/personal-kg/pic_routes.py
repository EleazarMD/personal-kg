"""PIC (Personal Identity Core) routes for identity, preferences, and goals."""

from fastapi import APIRouter, HTTPException
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from graph_store import GraphStore
from config import get_settings

settings = get_settings()
router = APIRouter(prefix="/api/pic", tags=["PIC"])

# Singleton PIC store instance
_pic_store = None


class PICStore:
    """Store for Personal Identity Core data using Neo4j."""
    
    def __init__(self, graph_store: GraphStore):
        self.graph = graph_store
        self._init_schema()
    
    def _init_schema(self):
        """Initialize PIC schema in Neo4j."""
        with self.graph.driver.session() as session:
            # Create constraints
            session.run("CREATE CONSTRAINT pic_identity IF NOT EXISTS FOR (i:Identity) REQUIRE i.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_preference IF NOT EXISTS FOR (p:Preference) REQUIRE p.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_goal IF NOT EXISTS FOR (g:Goal) REQUIRE g.id IS UNIQUE")
            session.run("CREATE CONSTRAINT pic_observation IF NOT EXISTS FOR (o:Observation) REQUIRE o.id IS UNIQUE")
    
    def get_identity(self) -> Optional[Dict[str, Any]]:
        """Get user identity."""
        with self.graph.driver.session() as session:
            result = session.run("MATCH (i:Identity) RETURN i LIMIT 1")
            record = result.single()
            if record:
                return dict(record["i"])
            return None
    
    def upsert_identity(self, identity_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update user identity."""
        with self.graph.driver.session() as session:
            session.run("""
                MERGE (i:Identity {id: 'user_identity'})
                SET i += $data,
                    i.updated_at = datetime()
                ON CREATE SET i.created_at = datetime()
            """, data=identity_data)
            return self.get_identity()
    
    def get_preferences(self) -> List[Dict[str, Any]]:
        """Get all preferences."""
        with self.graph.driver.session() as session:
            result = session.run("""
                MATCH (p:Preference)
                RETURN p
                ORDER BY p.created_at DESC
            """)
            return [dict(record["p"]) for record in result]
    
    def add_preference(self, category: str, key: str, value: Any, context: Optional[str] = None) -> Dict[str, Any]:
        """Add a preference."""
        import uuid
        pref_id = f"pref_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (p:Preference {
                    id: $id,
                    category: $category,
                    key: $key,
                    value: $value,
                    context: $context,
                    created_at: datetime()
                })
            """, id=pref_id, category=category, key=key, value=value, context=context)
            
            result = session.run("MATCH (p:Preference {id: $id}) RETURN p", id=pref_id)
            record = result.single()
            return dict(record["p"]) if record else {}
    
    def get_goals(self) -> List[Dict[str, Any]]:
        """Get all goals."""
        with self.graph.driver.session() as session:
            result = session.run("""
                MATCH (g:Goal)
                RETURN g
                ORDER BY g.priority DESC, g.created_at DESC
            """)
            return [dict(record["g"]) for record in result]
    
    def add_goal(self, title: str, description: Optional[str] = None, priority: int = 5, deadline: Optional[str] = None) -> Dict[str, Any]:
        """Add a goal."""
        import uuid
        goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (g:Goal {
                    id: $id,
                    title: $title,
                    description: $description,
                    priority: $priority,
                    deadline: $deadline,
                    status: 'active',
                    created_at: datetime()
                })
            """, id=goal_id, title=title, description=description, priority=priority, deadline=deadline)
            
            result = session.run("MATCH (g:Goal {id: $id}) RETURN g", id=goal_id)
            record = result.single()
            return dict(record["g"]) if record else {}
    
    def record_observation(self, observation: str, category: Optional[str] = None, source: Optional[str] = None) -> Dict[str, Any]:
        """Record an observation about the user."""
        import uuid
        obs_id = f"obs_{uuid.uuid4().hex[:12]}"
        
        with self.graph.driver.session() as session:
            session.run("""
                CREATE (o:Observation {
                    id: $id,
                    observation: $observation,
                    category: $category,
                    source: $source,
                    created_at: datetime()
                })
            """, id=obs_id, observation=observation, category=category, source=source)
            
            result = session.run("MATCH (o:Observation {id: $id}) RETURN o", id=obs_id)
            record = result.single()
            return dict(record["o"]) if record else {}
    
    def get_pic_stats(self) -> Dict[str, int]:
        """Get PIC statistics."""
        with self.graph.driver.session() as session:
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


def get_pic_store() -> PICStore:
    """Get or create PIC store singleton."""
    global _pic_store
    if _pic_store is None:
        from graph_store import GraphStore
        graph = GraphStore()
        graph.connect()
        _pic_store = PICStore(graph)
    return _pic_store


# Request/Response Models
class IdentityUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    roles: Optional[List[str]] = None
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
async def get_identity():
    """Get user identity."""
    store = get_pic_store()
    identity = store.get_identity()
    if not identity:
        return {"message": "No identity configured"}
    return identity


@router.put("/identity")
async def update_identity(data: IdentityUpdate):
    """Update user identity."""
    store = get_pic_store()
    identity_data = data.model_dump(exclude_none=True)
    updated = store.upsert_identity(identity_data)
    return {"status": "updated", "identity": updated}


@router.get("/preferences")
async def get_preferences():
    """Get all preferences."""
    store = get_pic_store()
    preferences = store.get_preferences()
    return {"preferences": preferences, "count": len(preferences)}


@router.post("/preferences")
async def add_preference(data: PreferenceCreate):
    """Add a preference."""
    store = get_pic_store()
    preference = store.add_preference(
        category=data.category,
        key=data.key,
        value=data.value,
        context=data.context
    )
    return {"status": "created", "preference": preference}


@router.get("/goals")
async def get_goals():
    """Get all goals."""
    store = get_pic_store()
    goals = store.get_goals()
    return {"goals": goals, "count": len(goals)}


@router.post("/goals")
async def add_goal(data: GoalCreate):
    """Add a goal."""
    store = get_pic_store()
    goal = store.add_goal(
        title=data.title,
        description=data.description,
        priority=data.priority,
        deadline=data.deadline
    )
    return {"status": "created", "goal": goal}


@router.post("/learn")
async def record_observation(data: ObservationCreate):
    """Record an observation (learning endpoint for agents)."""
    store = get_pic_store()
    observation = store.record_observation(
        observation=data.observation,
        category=data.category,
        source=data.source
    )
    return {"status": "recorded", "observation": observation}


@router.get("/context")
async def get_full_context():
    """Get complete PIC context for agent initialization."""
    store = get_pic_store()
    
    identity = store.get_identity()
    preferences = store.get_preferences()
    goals = store.get_goals()
    
    return {
        "identity": identity,
        "preferences": preferences,
        "goals": goals,
        "timestamp": datetime.utcnow().isoformat()
    }
