"""LIAM (Life Intelligence Augmentation Matrix) routes."""

import json
import os
import re
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from liam_models import (
    LIAMDimension, Framework, DimensionQuery, FrameworkQuery, 
    FrameworkRecommendation, DimensionGroup, DimensionStatus
)

router = APIRouter(prefix="/api/liam", tags=["LIAM"])

# Load LIAM dimensions from JSON
LIAM_DATA_PATH = os.path.join(os.path.dirname(__file__), "liam_data.json")
FRAMEWORKS_DATA_PATH = os.path.join(os.path.dirname(__file__), "frameworks.json")

def load_liam_data():
    """Load LIAM dimensions from JSON file."""
    with open(LIAM_DATA_PATH, 'r') as f:
        data = json.load(f)
    return [LIAMDimension(**dim) for dim in data["dimensions"]]

def load_frameworks_data():
    """Load LIAM frameworks from JSON file."""
    with open(FRAMEWORKS_DATA_PATH, 'r') as f:
        data = json.load(f)
    return [Framework(**fw) for fw in data["frameworks"]]

def save_frameworks_data(frameworks: List[Framework]):
    """Save LIAM frameworks to JSON file."""
    with open(FRAMEWORKS_DATA_PATH, 'r') as f:
        data = json.load(f)
    
    data["frameworks"] = [fw.dict() for fw in frameworks]
    
    with open(FRAMEWORKS_DATA_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    
    # Invalidate cache
    global _frameworks_cache
    _frameworks_cache = None

# Cache in memory
_dimensions_cache = None
_frameworks_cache = None

def get_dimensions() -> List[LIAMDimension]:
    """Get all LIAM dimensions (cached)."""
    global _dimensions_cache
    if _dimensions_cache is None:
        _dimensions_cache = load_liam_data()
    return _dimensions_cache

def get_frameworks() -> List[Framework]:
    """Get all LIAM frameworks (cached)."""
    global _frameworks_cache
    if _frameworks_cache is None:
        _frameworks_cache = load_frameworks_data()
    return _frameworks_cache


def _normalize_search_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _framework_search_blob(framework: Framework) -> str:
    return _normalize_search_text(" ".join([
        framework.id,
        framework.name,
        framework.source,
        framework.category,
        framework.description,
        framework.when_to_use,
        framework.limitations,
        " ".join(framework.key_concepts),
        " ".join(framework.applicable_dimensions),
    ]))


def _dimension_search_blob(dimension: LIAMDimension) -> str:
    return _normalize_search_text(" ".join([
        dimension.id,
        dimension.label,
        dimension.short,
        dimension.group.value,
        dimension.description,
        dimension.why_it_matters,
        dimension.scientific_basis,
        " ".join(dimension.model_thinker_models),
        " ".join(dimension.key_insights),
        " ".join(dimension.related_dimensions),
    ]))


@router.get("/dimensions", response_model=List[LIAMDimension])
async def list_dimensions(
    group: Optional[DimensionGroup] = None,
    status: Optional[DimensionStatus] = None,
    source: Optional[str] = None,
    author: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100
):
    """Get all LIAM dimensions, optionally filtered."""
    dimensions = get_dimensions()
    
    if group:
        dimensions = [d for d in dimensions if d.group == group]
    
    if status:
        dimensions = [d for d in dimensions if d.status == status]

    source_filter = author or source
    if source_filter:
        source_lower = source_filter.lower()
        dimensions = [d for d in dimensions if source_lower in d.scientific_basis.lower()]

    if query:
        query_terms = set(_normalize_search_text(query).split())
        dimensions = [
            d for d in dimensions
            if query_terms and query_terms.issubset(set(_dimension_search_blob(d).split()))
        ]
    
    return dimensions[:max(1, min(limit, 500))]


@router.get("/dimensions/{dimension_id}", response_model=LIAMDimension)
async def get_dimension(dimension_id: str):
    """Get a specific LIAM dimension by ID."""
    dimensions = get_dimensions()
    
    for dim in dimensions:
        if dim.id == dimension_id:
            return dim
    
    raise HTTPException(status_code=404, detail=f"Dimension '{dimension_id}' not found")


@router.post("/query/dimensions")
async def query_applicable_dimensions(query: DimensionQuery):
    """
    Query which LIAM dimensions are applicable to a problem/decision.
    
    Returns dimensions ranked by relevance to the problem description.
    """
    dimensions = get_dimensions()
    
    # Simple keyword matching for now - can be enhanced with semantic search
    problem_lower = query.problem_description.lower()
    context_lower = (query.context or "").lower()
    combined_text = f"{problem_lower} {context_lower}"
    
    scored_dimensions = []
    for dim in dimensions:
        score = 0.0
        
        # Check if dimension keywords appear in problem
        if dim.id in combined_text:
            score += 2.0
        
        # Check description overlap
        desc_words = set(dim.description.lower().split())
        problem_words = set(combined_text.split())
        overlap = len(desc_words & problem_words)
        score += overlap * 0.1
        
        # Check related dimensions
        for related in dim.related_dimensions:
            if related in combined_text:
                score += 0.5
        
        if score > 0:
            scored_dimensions.append({
                "dimension": dim,
                "relevance_score": min(score / 5.0, 1.0),  # Normalize to 0-1
                "reasoning": f"Matches {int(overlap)} keywords from dimension description"
            })
    
    # Sort by score
    scored_dimensions.sort(key=lambda x: x["relevance_score"], reverse=True)
    
    return {
        "query": query.problem_description,
        "applicable_dimensions": scored_dimensions[:5],
        "total_matches": len(scored_dimensions)
    }


@router.get("/frameworks", response_model=List[Framework])
async def list_frameworks(
    category: Optional[str] = None,
    dimension: Optional[str] = None,
    source: Optional[str] = None,
    author: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100
):
    """Get all LIAM frameworks, optionally filtered by category, dimension, source/author, or query."""
    frameworks = get_frameworks()
    
    if category:
        category_lower = category.lower()
        frameworks = [f for f in frameworks if f.category.lower() == category_lower]
    
    if dimension:
        frameworks = [f for f in frameworks if dimension in f.applicable_dimensions]

    source_filter = author or source
    if source_filter:
        source_lower = source_filter.lower()
        frameworks = [f for f in frameworks if source_lower in f.source.lower()]

    if query:
        query_terms = set(_normalize_search_text(query).split())
        frameworks = [
            f for f in frameworks
            if query_terms and query_terms.issubset(set(_framework_search_blob(f).split()))
        ]
    
    return frameworks[:max(1, min(limit, 500))]


@router.get("/frameworks/{framework_id}", response_model=Framework)
async def get_framework(framework_id: str):
    """Get a specific LIAM framework by ID."""
    frameworks = get_frameworks()
    
    for fw in frameworks:
        if fw.id == framework_id:
            return fw
    
    raise HTTPException(status_code=404, detail=f"Framework '{framework_id}' not found")


@router.post("/frameworks", response_model=Framework)
async def create_framework(framework: Framework):
    """Add a new LIAM framework."""
    frameworks = get_frameworks()
    
    if any(fw.id == framework.id for fw in frameworks):
        raise HTTPException(status_code=409, detail=f"Framework '{framework.id}' already exists")
        
    frameworks.append(framework)
    save_frameworks_data(frameworks)
    
    return framework


@router.put("/frameworks/{framework_id}", response_model=Framework)
async def update_framework(framework_id: str, framework_update: Framework):
    """Update an existing LIAM framework."""
    if framework_id != framework_update.id:
        raise HTTPException(status_code=400, detail="Framework ID in path must match body")
        
    frameworks = get_frameworks()
    
    for i, fw in enumerate(frameworks):
        if fw.id == framework_id:
            frameworks[i] = framework_update
            save_frameworks_data(frameworks)
            return framework_update
            
    raise HTTPException(status_code=404, detail=f"Framework '{framework_id}' not found")


@router.post("/query/frameworks")
async def query_applicable_frameworks(query: FrameworkQuery):
    """
    Query which frameworks/models are applicable to a problem.
    
    Returns full framework details with relevance scores, ranked by relevance.
    Uses both dimension-level matching and framework-level content matching.
    """
    dimensions = get_dimensions()
    frameworks = get_frameworks()
    problem_lower = query.problem_description.lower()
    problem_words = set(problem_lower.split())
    
    # Build lookup: framework name -> Framework object
    fw_by_name = {}
    fw_by_id = {}
    for fw in frameworks:
        fw_by_name[fw.name.lower()] = fw
        fw_by_id[fw.id] = fw
    
    # Filter dimensions if specified
    if query.dimension_filter:
        dimensions = [d for d in dimensions if d.id in query.dimension_filter]
    
    # Score frameworks using both dimension matching and direct content matching
    framework_scores = {}
    
    # 1. Dimension-level matching (existing logic)
    for dim in dimensions:
        relevance = 0.0
        if dim.id in problem_lower:
            relevance = 1.0
        elif any(word in problem_lower for word in dim.description.lower().split()):
            relevance = 0.5
        
        if relevance > 0:
            for framework_name in dim.model_thinker_models:
                if framework_name not in framework_scores:
                    framework_scores[framework_name] = {
                        "score": 0.0,
                        "dimensions": [],
                        "insights": [],
                        "match_sources": []
                    }
                framework_scores[framework_name]["score"] += relevance
                framework_scores[framework_name]["dimensions"].append(dim.id)
                framework_scores[framework_name]["insights"].extend(dim.key_insights[:2])
                framework_scores[framework_name]["match_sources"].append("dimension_match")
    
    
    # 2. Direct framework content matching (new)
    for fw in frameworks:
        score = 0.0
        
        # Check if framework name or ID appears in problem
        if fw.name.lower() in problem_lower or fw.id in problem_lower:
            score += 2.0
        
        # Check description word overlap
        desc_words = set(fw.description.lower().split())
        overlap = len(desc_words & problem_words)
        score += overlap * 0.15
        
        # Check when_to_use overlap
        when_words = set(fw.when_to_use.lower().split())
        when_overlap = len(when_words & problem_words)
        score += when_overlap * 0.1
        
        # Check key_concepts overlap
        for concept in fw.key_concepts:
            concept_words = set(concept.lower().split())
            concept_overlap = len(concept_words & problem_words)
            if concept_overlap >= 2:
                score += 0.5
        
        # Check category match
        if fw.category.replace("_", " ") in problem_lower:
            score += 0.3
        
        if score > 0:
            fw_key = fw.name
            if fw_key not in framework_scores:
                framework_scores[fw_key] = {
                    "score": 0.0,
                    "dimensions": list(fw.applicable_dimensions),
                    "insights": [],
                    "match_sources": []
                }
            framework_scores[fw_key]["score"] += score
            framework_scores[fw_key]["dimensions"].extend(fw.applicable_dimensions)
            framework_scores[fw_key]["match_sources"].append("content_match")
    
    
    # Build response with full framework details
    recommendations = []
    for framework_name, data in sorted(framework_scores.items(), key=lambda x: x[1]["score"], reverse=True)[:query.limit]:
        # Look up full framework data
        fw_obj = fw_by_name.get(framework_name.lower()) or fw_by_id.get(framework_name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_"))
        
        rec = {
            "framework_name": framework_name,
            "relevance_score": min(data["score"] / 4.0, 1.0),
            "applicable_dimensions": list(set(data["dimensions"])),
            "key_insights": list(set(data["insights"])),
            "reasoning": f"Applicable to {len(set(data['dimensions']))} relevant dimensions",
            "match_sources": list(set(data["match_sources"]))
        }
        
        # Attach full framework content if available
        if fw_obj:
            rec["framework"] = fw_obj.dict()
        
        recommendations.append(rec)
    
    
    return {
        "query": query.problem_description,
        "frameworks": recommendations,
        "total_frameworks": len(framework_scores)
    }


@router.get("/stats")
async def get_liam_stats():
    """Get LIAM statistics."""
    dimensions = get_dimensions()
    frameworks = get_frameworks()
    
    stats = {
        "total_dimensions": len(dimensions),
        "total_frameworks": len(frameworks),
        "by_group": {},
        "by_status": {},
        "by_category": {},
        "total_workflows": 0,
        "operational_workflows": 0
    }
    
    for dim in dimensions:
        group_key = dim.group.value
        stats["by_group"][group_key] = stats["by_group"].get(group_key, 0) + 1
        
        status_key = dim.status.value
        stats["by_status"][status_key] = stats["by_status"].get(status_key, 0) + 1
        
        stats["total_workflows"] += len(dim.workflows)
        stats["operational_workflows"] += sum(1 for w in dim.workflows if w.status == "operational")
    
    for fw in frameworks:
        cat_key = fw.category
        stats["by_category"][cat_key] = stats["by_category"].get(cat_key, 0) + 1
    
    
    return stats
