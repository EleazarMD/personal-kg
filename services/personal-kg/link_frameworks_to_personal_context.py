import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from graph_store import GraphStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORKS_PATH = os.path.join(BASE_DIR, "frameworks.json")
LIAM_PATH = os.path.join(BASE_DIR, "liam_data.json")

MAX_LINKS_PER_PERSONAL_NODE = 5
MIN_SCORE = 3.5

PERSONAL_LABELS = {
    "Goal": ["title", "description", "status", "deadline"],
    "Preference": ["category", "key", "value", "context"],
    "Observation": ["observation", "category", "source"],
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "when", "what", "where",
    "your", "you", "are", "can", "use", "using", "into", "than", "then", "they",
    "their", "there", "will", "would", "should", "have", "has", "had", "not", "but",
    "all", "any", "each", "more", "less", "most", "about", "under", "over", "between",
    "framework", "model", "concept", "dimension", "system", "systems", "decision",
    "preference", "preferences", "value", "category", "context", "explicitly", "key",
    "other", "full", "primary", "order", "conversation", "source", "status", "created",
    "during", "u2014", "explicit", "explicitly", "classic", "matters", "level",
    "restaurant", "meals", "morning", "night", "saturday", "sunday",
}

ALIASES = {
    "financial": ["money", "portfolio", "investment", "investing", "budget", "wealth", "risk", "loss", "gain"],
    "clinical": ["patient", "clinic", "medical", "physician", "diagnosis", "care", "medicine"],
    "communication": ["email", "message", "reply", "tone", "draft", "recipient"],
    "family": ["kids", "children", "school", "pickup", "family", "parent"],
    "infrastructure": ["homelab", "service", "systemd", "gpu", "server", "downtime", "backup", "reliability"],
    "attention": ["focus", "notification", "distraction", "cognitive", "load"],
    "health": ["sleep", "exercise", "fitness", "wellness", "burnout", "nutrition"],
    "goals": ["goal", "milestone", "quarterly", "career", "priority"],
    "research": ["paper", "literature", "research", "publication", "knowledge"],
    "metacognition": ["bias", "thinking", "reasoning", "judgment", "blindspot"],
}


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]{2,}", text.lower()) if t not in STOPWORDS]


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def personal_text(node: Dict[str, Any], fields: Iterable[str]) -> str:
    return "\n".join(stringify(node.get(field)) for field in fields if node.get(field) is not None)


def framework_text(item: Dict[str, Any]) -> str:
    parts = [
        item.get("id", ""),
        item.get("name") or item.get("label", ""),
        item.get("category", ""),
        item.get("source", ""),
        item.get("description", ""),
        item.get("when_to_use", ""),
        item.get("limitations", ""),
        stringify(item.get("key_concepts") or item.get("key_insights") or []),
        stringify(item.get("applicable_dimensions") or item.get("related_dimensions") or []),
    ]
    return "\n".join(parts)


def load_candidates() -> List[Dict[str, Any]]:
    with open(FRAMEWORKS_PATH, "r") as f:
        frameworks = json.load(f)["frameworks"]
    with open(LIAM_PATH, "r") as f:
        dimensions = json.load(f)["dimensions"]

    candidates = []
    for fw in frameworks:
        candidates.append({
            "id": f"framework_{fw['id']}",
            "name": fw["name"],
            "kind": "framework",
            "source_id": fw["id"],
            "applicable_dimensions": fw.get("applicable_dimensions", []),
            "tokens": set(tokens(framework_text(fw))),
        })
    for dim in dimensions:
        dim_tokens = set(tokens(framework_text({
            "id": dim["id"],
            "label": dim["label"],
            "description": dim.get("description", ""),
            "key_insights": dim.get("key_insights", []),
            "related_dimensions": dim.get("related_dimensions", []),
        })))
        dim_tokens.update(tokens(dim.get("group", "")))
        candidates.append({
            "id": f"dimension_{dim['id']}",
            "name": dim["label"],
            "kind": "liam_dimension",
            "source_id": dim["id"],
            "applicable_dimensions": [dim["id"]],
            "tokens": dim_tokens,
        })
    return candidates


def score_candidate(personal_tokens: set, candidate: Dict[str, Any]) -> Tuple[float, List[str]]:
    overlap = personal_tokens & candidate["tokens"]
    score = float(len(overlap))

    for dim in candidate.get("applicable_dimensions", []):
        alias_hits = personal_tokens & set(ALIASES.get(dim, []))
        if alias_hits:
            score += 2.0 + len(alias_hits) * 0.5
            overlap = overlap | alias_hits

    return score, sorted(overlap)[:12]


def ensure_personal_links(gs: GraphStore, label: str, candidates: List[Dict[str, Any]]) -> int:
    fields = PERSONAL_LABELS[label]
    created = 0
    with gs.driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:{label})
            RETURN p
            ORDER BY coalesce(p.updated_at, p.created_at) DESC
            LIMIT 500
            """
        )
        personal_nodes = [dict(record["p"]) for record in result]

    for node in personal_nodes:
        node_id = node.get("id")
        if not node_id:
            continue
        p_text = personal_text(node, fields)
        p_tokens = set(tokens(p_text))
        if not p_tokens:
            continue

        scored = []
        for candidate in candidates:
            score, evidence = score_candidate(p_tokens, candidate)
            if score >= MIN_SCORE and evidence:
                scored.append((score, evidence, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)

        for score, evidence, candidate in scored[:MAX_LINKS_PER_PERSONAL_NODE]:
            with gs.driver.session() as session:
                res = session.run(
                    f"""
                    MATCH (p:{label} {{id: $personal_id}})
                    MATCH (f:Entity {{id: $framework_id}})
                    MERGE (p)-[r:GUIDED_BY]->(f)
                    ON CREATE SET
                        r.owner = 'pcg',
                        r.created_at = datetime(),
                        r.created_by = 'link_frameworks_to_personal_context',
                        r.score = $score,
                        r.evidence = $evidence,
                        r.framework_kind = $framework_kind
                    ON MATCH SET
                        r.updated_at = datetime(),
                        r.score = CASE WHEN coalesce(r.score, 0.0) < $score THEN $score ELSE r.score END,
                        r.evidence = $evidence,
                        r.framework_kind = $framework_kind
                    RETURN r.created_at = coalesce(r.updated_at, r.created_at) AS linked
                    """,
                    personal_id=node_id,
                    framework_id=candidate["id"],
                    score=score,
                    evidence=evidence,
                    framework_kind=candidate["kind"],
                )
                if res.single():
                    created += 1
                    print(f"{label}:{node_id} -> {candidate['name']} score={score:.1f} evidence={evidence}")
    return created


def main() -> None:
    gs = GraphStore()
    gs.connect()
    with gs.driver.session() as session:
        record = session.run("""
            MATCH ()-[r:GUIDED_BY {created_by: 'link_frameworks_to_personal_context'}]->()
            DELETE r
            RETURN count(r) AS deleted
        """).single()
        print(f"Cleared {record['deleted'] if record else 0} prior script-created GUIDED_BY edges.")

    candidates = load_candidates()
    total = 0
    for label in PERSONAL_LABELS:
        total += ensure_personal_links(gs, label, candidates)
    gs.close()
    print(f"Completed personal context linking pass. Evaluated {len(candidates)} candidates; wrote/updated {total} GUIDED_BY edges.")


if __name__ == "__main__":
    main()
