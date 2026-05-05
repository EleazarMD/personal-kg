import asyncio
import json
import uuid
import os
from models import Entity, EntityType, Relationship, RelationshipType
from graph_store import GraphStore
from vector_store import VectorStore

async def main():
    print("Starting full library synchronization...")
    
    # 1. Initialize Stores
    gs = GraphStore()
    gs.connect()
    vs = VectorStore()
    vs.connect()

    # 2. Load JSON Data
    base_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_dir, "frameworks.json"), "r") as f:
        frameworks_data = json.load(f)["frameworks"]
        
    with open(os.path.join(base_dir, "liam_data.json"), "r") as f:
        liam_data = json.load(f)["dimensions"]

    print(f"Loaded {len(frameworks_data)} frameworks and {len(liam_data)} LIAM dimensions.")

    entity_map = {}

    # 3. Process LIAM Dimensions
    for dim in liam_data:
        dim_id = f"dimension_{dim['id']}"
        props = {
            "group": dim["group"],
            "short_label": dim["short"],
            "why_it_matters": dim.get("why_it_matters", ""),
            "scientific_basis": dim.get("scientific_basis", ""),
            "key_insights": dim.get("key_insights", []),
            "status": dim.get("status", "planned")
        }
        
        e = Entity(
            id=dim_id,
            name=dim["label"],
            type=EntityType.CONCEPT,
            description=f"[LIAM Dimension] {dim['description']}",
            properties=props
        )
        
        # Format text specifically for embedding semantic search
        embed_text = f"LIAM Dimension: {e.name}\n"
        embed_text += f"Description: {e.description}\n"
        embed_text += f"Why it matters: {props['why_it_matters']}\n"
        embed_text += f"Scientific Basis: {props['scientific_basis']}\n"
        embed_text += "Key Insights:\n- " + "\n- ".join(props["key_insights"])
        
        stored = gs.upsert_entity(e)
        # Assuming vs.add_entity can take a raw entity. The vector store handles its own text formatting or we inject it into properties.
        # Let's write the embed_text to a property to ensure the vector store catches it.
        e.properties["_search_text"] = embed_text
        vs.add_entity(stored)
        
        entity_map[dim["id"]] = stored.id
        print(f"Upserted Dimension: {dim['label']}")

    # 4. Process Frameworks
    for fw in frameworks_data:
        fw_id = f"framework_{fw['id']}"
        props = {
            "source": fw.get("source", ""),
            "category": fw.get("category", ""),
            "when_to_use": fw.get("when_to_use", ""),
            "limitations": fw.get("limitations", ""),
            "key_concepts": fw.get("key_concepts", []),
        }
        
        e = Entity(
            id=fw_id,
            name=fw["name"],
            type=EntityType.CONCEPT,
            description=f"[Mental Model / Framework] {fw['description']}",
            properties=props
        )
        
        # Format text for high-quality semantic vector search
        embed_text = f"Framework: {e.name}\n"
        embed_text += f"Category: {props['category']} | Source: {props['source']}\n"
        embed_text += f"Description: {e.description}\n"
        embed_text += f"When to use: {props['when_to_use']}\n"
        embed_text += f"Limitations: {props['limitations']}\n"
        embed_text += "Key Concepts:\n- " + "\n- ".join(props["key_concepts"])
        
        e.properties["_search_text"] = embed_text
        
        stored = gs.upsert_entity(e)
        vs.add_entity(stored)
        
        entity_map[fw["id"]] = stored.id
        print(f"Upserted Framework: {fw['name']}")

        # 5. Create Relationships between Frameworks and Dimensions
        # Frameworks specify which dimensions they apply to
        if "applicable_dimensions" in fw:
            for applicable_dim in fw["applicable_dimensions"]:
                if applicable_dim in entity_map:
                    rel = Relationship(
                        source_entity=stored.id,
                        target_entity=entity_map[applicable_dim],
                        type=RelationshipType.RELATED_TO,
                        weight=1.0,
                        description="Framework applies to LIAM dimension"
                    )
                    gs.create_relationship(rel)

    gs.close()
    print("Synchronization complete!")

if __name__ == "__main__":
    asyncio.run(main())
