import asyncio
from models import Entity, EntityType, Relationship, RelationshipType
from graph_store import GraphStore

async def main():
    gs = GraphStore()
    gs.connect()
    
    # Fetch all entities to find their IDs
    all_entities = gs.list_entities(limit=1000)
    entity_map = {e.name.lower(): e for e in all_entities}
    
    # Helper to get or create an entity if it was only in frameworks.json and not yet in Neo4j
    def get_or_create(name, etype=EntityType.CONCEPT):
        key = name.lower()
        if key in entity_map:
            return entity_map[key]
        else:
            e = Entity(name=name, type=etype, description=f"Auto-created from frameworks: {name}")
            stored = gs.upsert_entity(e)
            entity_map[key] = stored
            print(f"Auto-created missing entity: {name}")
            return stored

    # Old Frameworks
    satisficing = get_or_create("Satisficing")
    value_function = get_or_create("Value Function")
    sandpile = get_or_create("Sandpile / Self-Organized Criticality")
    cascade = get_or_create("Cascade")
    redundancy = get_or_create("Redundancy")
    attention = get_or_create("Attention Mechanism")
    monte_carlo = get_or_create("Monte Carlo Simulation")
    
    # New Kahneman / Taleb Concepts
    dual_system = get_or_create("Dual-System Theory")
    heuristics = get_or_create("Cognitive Heuristics & Biases")
    prospect_theory = get_or_create("Prospect Theory")
    black_swan = get_or_create("Black Swan Theory")
    antifragility = get_or_create("Antifragility")
    barbell = get_or_create("Barbell Strategy")
    
    # Define Cross-Relationships
    rels = [
        # Kahneman connections to existing models
        (heuristics.id, satisficing.id, RelationshipType.RELATED_TO),  # Both deal with bounded rationality
        (value_function.id, prospect_theory.id, RelationshipType.PART_OF), # Value function is the core of Prospect Theory
        (attention.id, dual_system.id, RelationshipType.USES), # System 2 requires attention mechanisms
        
        # Taleb connections to existing models
        (black_swan.id, sandpile.id, RelationshipType.RELATED_TO), # Sandpile/SOC explains Extremistan/Black Swan environments
        (black_swan.id, cascade.id, RelationshipType.RELATED_TO), # Cascades cause Black Swans
        (antifragility.id, redundancy.id, RelationshipType.RELATED_TO), # Redundancy is robust, Antifragility goes beyond it
        (barbell.id, redundancy.id, RelationshipType.USES), # Barbell uses redundancy on the safe end
        (monte_carlo.id, black_swan.id, RelationshipType.RELATED_TO), # Monte Carlo struggles to model Black Swans (Fat Tails)
    ]
    
    for src, tgt, rtype in rels:
        rel = Relationship(
            source_entity=src,
            target_entity=tgt,
            type=rtype,
            weight=1.0,
            description="Cross-framework synthesis"
        )
        gs.create_relationship(rel)
        print(f"Created cross-relationship: {src} -[{rtype.value}]-> {tgt}")
        
    gs.close()

if __name__ == "__main__":
    asyncio.run(main())
