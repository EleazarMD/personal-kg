import asyncio
from models import Entity, EntityType, Relationship, RelationshipType
from graph_store import GraphStore
from vector_store import VectorStore

async def main():
    gs = GraphStore()
    gs.connect()
    
    vs = VectorStore()
    vs.connect()
    
    # 1. Create Persons
    persons = {
        "kahneman": Entity(name="Daniel Kahneman", type=EntityType.PERSON, description="Psychologist, economist, and author of Thinking, Fast and Slow."),
        "tversky": Entity(name="Amos Tversky", type=EntityType.PERSON, description="Cognitive and mathematical psychologist, collaborated heavily with Kahneman."),
        "taleb": Entity(name="Nassim Nicholas Taleb", type=EntityType.PERSON, description="Author of the Incerto series, statistician, and risk analyst.")
    }
    
    # 2. Create Concepts
    concepts = {
        "dual_system": Entity(name="Dual-System Theory", type=EntityType.CONCEPT, description="Framework splitting cognition into automatic (System 1) and deliberative (System 2) thinking."),
        "system_1": Entity(name="System 1 Thinking", type=EntityType.CONCEPT, description="Fast, automatic, frequent, emotional, stereotypic, subconscious thinking."),
        "system_2": Entity(name="System 2 Thinking", type=EntityType.CONCEPT, description="Slow, effortful, infrequent, logical, calculating, conscious thinking."),
        "wysiati": Entity(name="WYSIATI", type=EntityType.CONCEPT, description="What You See Is All There Is - the tendency for System 1 to draw conclusions from available information while ignoring missing data."),
        "heuristics": Entity(name="Cognitive Heuristics & Biases", type=EntityType.CONCEPT, description="Mental shortcuts that simplify decisions but lead to predictable irrationalities."),
        "availability": Entity(name="Availability Heuristic", type=EntityType.CONCEPT, description="Judging probabilities based on ease of recall (e.g. vivid memories)."),
        "anchoring": Entity(name="Anchoring and Adjustment", type=EntityType.CONCEPT, description="Over-relying on an initial piece of information (anchor) when making decisions."),
        "representativeness": Entity(name="Representativeness Heuristic", type=EntityType.CONCEPT, description="Judging probabilities by similarity to a prototype, neglecting base rates."),
        "prospect_theory": Entity(name="Prospect Theory", type=EntityType.CONCEPT, description="Behavioral economic theory that models decisions under risk, showing losses loom larger than gains."),
        "loss_aversion": Entity(name="Loss Aversion", type=EntityType.CONCEPT, description="The psychological preference for avoiding losses over acquiring equivalent gains."),
        "incerto": Entity(name="Incerto Series", type=EntityType.CONCEPT, description="A five-volume series by Nassim Taleb investigating opacity, luck, uncertainty, and probability."),
        "antifragility": Entity(name="Antifragility", type=EntityType.CONCEPT, description="Systems that increase in capability or resilience as a result of stressors, shocks, or volatility."),
        "black_swan": Entity(name="Black Swan Theory", type=EntityType.CONCEPT, description="The extreme impact of rare and unpredictable outlier events, and the human tendency to find simplistic explanations for them retrospectively."),
        "barbell": Entity(name="Barbell Strategy", type=EntityType.CONCEPT, description="Bimodal allocation combining extreme risk aversion on one end and extreme risk loving on the other, avoiding the middle."),
        "lindy": Entity(name="Lindy Effect", type=EntityType.CONCEPT, description="The future life expectancy of non-perishable things is proportional to their current age."),
        "via_negativa": Entity(name="Via Negativa", type=EntityType.CONCEPT, description="Improvement by subtraction; removing harmful elements rather than adding new ones."),
        "skin_in_game": Entity(name="Skin in the Game", type=EntityType.CONCEPT, description="Symmetry of risk; decision-makers must bear personal consequences of their actions.")
    }
    
    # Store all and save their actual IDs
    stored_entities = {}
    for key, entity in {**persons, **concepts}.items():
        stored = gs.upsert_entity(entity)
        vs.add_entity(stored)
        stored_entities[key] = stored
        print(f"Stored entity: {stored.name} ({stored.id})")
        
    # 3. Create Relationships
    rels = [
        # Kahneman & Tversky -> Concepts
        (stored_entities["kahneman"].id, stored_entities["dual_system"].id, RelationshipType.CREATED_BY),
        (stored_entities["kahneman"].id, stored_entities["prospect_theory"].id, RelationshipType.CREATED_BY),
        (stored_entities["kahneman"].id, stored_entities["heuristics"].id, RelationshipType.CREATED_BY),
        (stored_entities["tversky"].id, stored_entities["prospect_theory"].id, RelationshipType.CREATED_BY),
        (stored_entities["tversky"].id, stored_entities["heuristics"].id, RelationshipType.CREATED_BY),
        (stored_entities["kahneman"].id, stored_entities["tversky"].id, RelationshipType.KNOWS),
        
        # Dual System Hierarchy
        (stored_entities["system_1"].id, stored_entities["dual_system"].id, RelationshipType.PART_OF),
        (stored_entities["system_2"].id, stored_entities["dual_system"].id, RelationshipType.PART_OF),
        (stored_entities["wysiati"].id, stored_entities["system_1"].id, RelationshipType.RELATED_TO),
        (stored_entities["system_1"].id, stored_entities["heuristics"].id, RelationshipType.LEADS_TO),
        
        # Heuristics Hierarchy
        (stored_entities["availability"].id, stored_entities["heuristics"].id, RelationshipType.PART_OF),
        (stored_entities["anchoring"].id, stored_entities["heuristics"].id, RelationshipType.PART_OF),
        (stored_entities["representativeness"].id, stored_entities["heuristics"].id, RelationshipType.PART_OF),
        
        # Prospect Theory Hierarchy
        (stored_entities["loss_aversion"].id, stored_entities["prospect_theory"].id, RelationshipType.PART_OF),
        
        # Taleb -> Concepts
        (stored_entities["taleb"].id, stored_entities["incerto"].id, RelationshipType.CREATED_BY),
        (stored_entities["antifragility"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        (stored_entities["black_swan"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        (stored_entities["barbell"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        (stored_entities["lindy"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        (stored_entities["via_negativa"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        (stored_entities["skin_in_game"].id, stored_entities["incerto"].id, RelationshipType.PART_OF),
        
        # Taleb Concept Interconnections
        (stored_entities["black_swan"].id, stored_entities["antifragility"].id, RelationshipType.RELATED_TO),
        (stored_entities["barbell"].id, stored_entities["antifragility"].id, RelationshipType.USES),
        (stored_entities["via_negativa"].id, stored_entities["antifragility"].id, RelationshipType.LEADS_TO),
        (stored_entities["skin_in_game"].id, stored_entities["antifragility"].id, RelationshipType.REQUIRES),
    ]
    
    for src, tgt, rtype in rels:
        rel = Relationship(
            source_entity=src,
            target_entity=tgt,
            type=rtype,
            weight=1.0
        )
        gs.create_relationship(rel)
        print(f"Created relationship: {src} -[{rtype.value}]-> {tgt}")
        
    gs.close()

if __name__ == "__main__":
    asyncio.run(main())
