"""Neo4j graph store for knowledge graph and PIC data."""

import uuid
from typing import List, Optional, Dict, Any
from neo4j import GraphDatabase
from config import get_settings
from models import Entity, Relationship, Community, EntityType, RelationshipType

settings = get_settings()


class GraphStore:
    """Neo4j graph database store."""
    
    def __init__(self):
        self.driver = None
        self.settings = settings
    
    def connect(self):
        """Connect to Neo4j."""
        self.driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        )
        
        with self.driver.session() as session:
            session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
            session.run("CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
            session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")
    
    def close(self):
        """Close connection."""
        if self.driver:
            self.driver.close()
    
    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity)
                WITH count(e) as entities
                MATCH ()-[r]->()
                WITH entities, count(r) as relationships
                MATCH (c:Community)
                RETURN entities, relationships, count(c) as communities
            """)
            record = result.single()
            if record:
                return {
                    "entities": record["entities"],
                    "relationships": record["relationships"],
                    "communities": record["communities"]
                }
            return {"entities": 0, "relationships": 0, "communities": 0}
    
    def create_document(self, doc_id: str, title: str, source: Optional[str], metadata: Dict[str, Any]):
        """Create a document node."""
        with self.driver.session() as session:
            session.run("""
                CREATE (d:Document {
                    id: $id,
                    title: $title,
                    source: $source,
                    metadata: $metadata,
                    created_at: datetime()
                })
            """, id=doc_id, title=title, source=source, metadata=metadata)
    
    def upsert_entity(self, entity: Entity) -> Entity:
        """Create or update an entity."""
        if not entity.id:
            entity.id = f"entity_{uuid.uuid4().hex[:12]}"
        
        with self.driver.session() as session:
            session.run("""
                MERGE (e:Entity {id: $id})
                SET e.name = $name,
                    e.type = $type,
                    e.description = $description,
                    e.properties = $properties,
                    e.source_document_id = $source_document_id,
                    e.updated_at = datetime()
                ON CREATE SET e.created_at = datetime()
            """, 
                id=entity.id,
                name=entity.name,
                type=entity.type.value,
                description=entity.description,
                properties=entity.properties,
                source_document_id=entity.source_document_id
            )
        
        return entity
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $id})
                RETURN e
            """, id=entity_id)
            
            record = result.single()
            if record:
                node = record["e"]
                return Entity(
                    id=node["id"],
                    name=node["name"],
                    type=EntityType(node["type"]),
                    description=node.get("description"),
                    properties=node.get("properties", {}),
                    source_document_id=node.get("source_document_id")
                )
            return None
    
    def list_entities(self, entity_type: Optional[EntityType] = None, limit: int = 50, offset: int = 0) -> List[Entity]:
        """List entities with optional filtering."""
        with self.driver.session() as session:
            if entity_type:
                result = session.run("""
                    MATCH (e:Entity {type: $type})
                    RETURN e
                    ORDER BY e.created_at DESC
                    SKIP $offset
                    LIMIT $limit
                """, type=entity_type.value, offset=offset, limit=limit)
            else:
                result = session.run("""
                    MATCH (e:Entity)
                    RETURN e
                    ORDER BY e.created_at DESC
                    SKIP $offset
                    LIMIT $limit
                """, offset=offset, limit=limit)
            
            entities = []
            for record in result:
                node = record["e"]
                entities.append(Entity(
                    id=node["id"],
                    name=node["name"],
                    type=EntityType(node["type"]),
                    description=node.get("description"),
                    properties=node.get("properties", {}),
                    source_document_id=node.get("source_document_id")
                ))
            return entities
    
    def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $id})
                DETACH DELETE e
                RETURN count(e) as deleted
            """, id=entity_id)
            record = result.single()
            return record["deleted"] > 0 if record else False
    
    def create_relationship(self, relationship: Relationship) -> Relationship:
        """Create a relationship between entities."""
        if not relationship.id:
            relationship.id = f"rel_{uuid.uuid4().hex[:12]}"
        
        with self.driver.session() as session:
            session.run("""
                MATCH (source:Entity {id: $source_id})
                MATCH (target:Entity {id: $target_id})
                CREATE (source)-[r:RELATES {
                    id: $id,
                    type: $type,
                    description: $description,
                    weight: $weight,
                    properties: $properties,
                    source_document_id: $source_document_id,
                    created_at: datetime()
                }]->(target)
            """,
                id=relationship.id,
                source_id=relationship.source_entity,
                target_id=relationship.target_entity,
                type=relationship.type.value,
                description=relationship.description,
                weight=relationship.weight,
                properties=relationship.properties,
                source_document_id=relationship.source_document_id
            )
        
        return relationship
    
    def get_relationships(self, entity_id: Optional[str] = None, relationship_type: Optional[RelationshipType] = None) -> List[Relationship]:
        """Get relationships, optionally filtered."""
        with self.driver.session() as session:
            if entity_id:
                result = session.run("""
                    MATCH (source:Entity {id: $entity_id})-[r:RELATES]->(target:Entity)
                    RETURN r, source.id as source_id, target.id as target_id
                """, entity_id=entity_id)
            else:
                result = session.run("""
                    MATCH (source:Entity)-[r:RELATES]->(target:Entity)
                    RETURN r, source.id as source_id, target.id as target_id
                    LIMIT 1000
                """)
            
            relationships = []
            for record in result:
                rel = record["r"]
                rel_type = RelationshipType(rel["type"]) if "type" in rel else RelationshipType.OTHER
                
                if relationship_type and rel_type != relationship_type:
                    continue
                
                relationships.append(Relationship(
                    id=rel.get("id", ""),
                    source_entity=record["source_id"],
                    target_entity=record["target_id"],
                    type=rel_type,
                    description=rel.get("description"),
                    weight=rel.get("weight", 1.0),
                    properties=rel.get("properties", {}),
                    source_document_id=rel.get("source_document_id")
                ))
            return relationships
    
    def link_entity_to_document(self, entity_id: str, document_id: str):
        """Link an entity to its source document."""
        with self.driver.session() as session:
            session.run("""
                MATCH (e:Entity {id: $entity_id})
                MATCH (d:Document {id: $document_id})
                MERGE (e)-[:EXTRACTED_FROM]->(d)
            """, entity_id=entity_id, document_id=document_id)
    
    def get_entity_neighbors(self, entity_id: str, depth: int = 1) -> Dict[str, Any]:
        """Get an entity and its neighbors."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (e:Entity {id: $entity_id})
                OPTIONAL MATCH (e)-[*1..$depth]-(neighbor:Entity)
                RETURN e, collect(DISTINCT neighbor) as neighbors
            """, entity_id=entity_id, depth=depth)
            
            record = result.single()
            if not record:
                return {"entity": None, "neighbors": [], "neighbor_count": 0}
            
            entity_node = record["e"]
            entity = Entity(
                id=entity_node["id"],
                name=entity_node["name"],
                type=EntityType(entity_node["type"]),
                description=entity_node.get("description"),
                properties=entity_node.get("properties", {})
            )
            
            neighbors = []
            for neighbor_node in record["neighbors"]:
                if neighbor_node:
                    neighbors.append(Entity(
                        id=neighbor_node["id"],
                        name=neighbor_node["name"],
                        type=EntityType(neighbor_node["type"]),
                        description=neighbor_node.get("description"),
                        properties=neighbor_node.get("properties", {})
                    ))
            
            return {
                "entity": entity,
                "neighbors": neighbors,
                "neighbor_count": len(neighbors)
            }
    
    def create_community(self, community: Community):
        """Create a community."""
        with self.driver.session() as session:
            session.run("""
                CREATE (c:Community {
                    id: $id,
                    name: $name,
                    summary: $summary,
                    entity_ids: $entity_ids,
                    level: $level,
                    created_at: datetime()
                })
            """,
                id=community.id,
                name=community.name,
                summary=community.summary,
                entity_ids=community.entity_ids,
                level=community.level
            )
    
    def get_communities(self, level: Optional[int] = None) -> List[Community]:
        """Get communities."""
        with self.driver.session() as session:
            if level is not None:
                result = session.run("""
                    MATCH (c:Community {level: $level})
                    RETURN c
                """, level=level)
            else:
                result = session.run("""
                    MATCH (c:Community)
                    RETURN c
                """)
            
            communities = []
            for record in result:
                node = record["c"]
                communities.append(Community(
                    id=node["id"],
                    name=node["name"],
                    summary=node["summary"],
                    entity_ids=node.get("entity_ids", []),
                    level=node.get("level", 0)
                ))
            return communities
