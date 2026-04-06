"""LIAM (Life Intelligence Augmentation Matrix) routes."""

import json
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from liam_models import (
    LIAMDimension, Framework, DimensionQuery, FrameworkQuery, 
    FrameworkRecommendation, DimensionGroup, DimensionStatus
)

router = APIRouter(prefix="/api/liam", tags=["LIAM"])

# Load LIAM dimensions from JSON
LIAM_DATA_PATH = os.path.join(os.path.dirname(__file__), "liam_data.json")

def load_liam_data():
    """Load LIAM dimensions from JSON file."""
    with open(LIAM_DATA_PATH, 'r') as f:
        data = json.load(f)
    return [LIAMDimension(**dim) for dim in data["dimensions"]]

# Cache dimensions in memory
_dimensions_cache = None

def get_dimensions() -> List[LIAMDimension]:
    """Get all LIAM dimensions (cached)."""
    global _dimensions_cache
    if _dimensions_cache is None:
        _dimensions_cache = load_liam_data()
    return _dimensions_cache


@router.get("/dimensions", response_model=List[LIAMDimension])
async def list_dimensions(
    group: Optional[DimensionGroup] = None,
    status: Optional[DimensionStatus] = None
):
    """Get all LIAM dimensions, optionally filtered."""
    dimensions = get_dimensions()
    
    if group:
        dimensions = [d for d in dimensions if d.group == group]
    
    if status:
        dimensions = [d for d in dimensions if d.status == status]
    
    return dimensions


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


@router.post("/query/frameworks")
async def query_applicable_frameworks(query: FrameworkQuery):
    """
    Query which frameworks/models are applicable to a problem.
    
    Returns Model Thinker frameworks and other mental models ranked by relevance.
    """
    dimensions = get_dimensions()
    
    # Filter dimensions if specified
    if query.dimension_filter:
        dimensions = [d for d in dimensions if d.id in query.dimension_filter]
    
    # Collect all unique frameworks from applicable dimensions
    problem_lower = query.problem_description.lower()
    
    framework_scores = {}
    for dim in dimensions:
        # Simple relevance check
        relevance = 0.0
        if dim.id in problem_lower:
            relevance = 1.0
        elif any(word in problem_lower for word in dim.description.lower().split()):
            relevance = 0.5
        
        if relevance > 0:
            for framework in dim.model_thinker_models:
                if framework not in framework_scores:
                    framework_scores[framework] = {
                        "score": 0.0,
                        "dimensions": [],
                        "insights": []
                    }
                framework_scores[framework]["score"] += relevance
                framework_scores[framework]["dimensions"].append(dim.id)
                framework_scores[framework]["insights"].extend(dim.key_insights[:2])
    
    # Build response
    recommendations = []
    for framework_name, data in sorted(framework_scores.items(), key=lambda x: x[1]["score"], reverse=True)[:query.limit]:
        recommendations.append({
            "framework_name": framework_name,
            "relevance_score": min(data["score"] / 2.0, 1.0),
            "applicable_dimensions": list(set(data["dimensions"])),
            "key_insights": list(set(data["insights"])),
            "reasoning": f"Applicable to {len(set(data['dimensions']))} relevant dimensions"
        })
    
    return {
        "query": query.problem_description,
        "frameworks": recommendations,
        "total_frameworks": len(framework_scores)
    }


@router.get("/stats")
async def get_liam_stats():
    """Get LIAM statistics."""
    dimensions = get_dimensions()
    
    stats = {
        "total_dimensions": len(dimensions),
        "by_group": {},
        "by_status": {},
        "total_workflows": 0,
        "operational_workflows": 0
    }
    
    for dim in dimensions:
        # Count by group
        group_key = dim.group.value
        stats["by_group"][group_key] = stats["by_group"].get(group_key, 0) + 1
        
        # Count by status
        status_key = dim.status.value
        stats["by_status"][status_key] = stats["by_status"].get(status_key, 0) + 1
        
        # Count workflows
        stats["total_workflows"] += len(dim.workflows)
        stats["operational_workflows"] += sum(1 for w in dim.workflows if w.status == "operational")
    
    return stats
