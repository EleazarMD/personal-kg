"""LIAM (Life Intelligence Augmentation Matrix) data models."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DimensionGroup(str, Enum):
    """LIAM dimension groups."""
    CORE = "core"
    COGNITIVE = "cognitive"


class DimensionStatus(str, Enum):
    """LIAM dimension implementation status."""
    OPERATIONAL = "operational"
    IN_PROGRESS = "in-progress"
    PLANNED = "planned"
    FUTURE = "future"


class WorkflowStatus(BaseModel):
    """Workflow status for a dimension."""
    name: str
    status: str


class LIAMDimension(BaseModel):
    """LIAM dimension - a life area with frameworks and workflows."""
    id: str = Field(..., description="Dimension ID (e.g., 'clinical', 'communication')")
    label: str = Field(..., description="Full dimension name")
    short: str = Field(..., description="Short label")
    icon: str = Field(..., description="Emoji icon")
    accent: str = Field(..., description="Color accent (hex)")
    group: DimensionGroup = Field(..., description="Dimension group")
    description: str = Field(..., description="What this dimension covers")
    why_it_matters: str = Field(..., description="Why this dimension is important")
    scientific_basis: str = Field(..., description="Scientific/research foundation")
    model_thinker_models: List[str] = Field(default_factory=list, description="Applicable Model Thinker frameworks")
    key_insights: List[str] = Field(default_factory=list, description="Key insights for this dimension")
    related_dimensions: List[str] = Field(default_factory=list, description="Related dimension IDs")
    status: DimensionStatus = Field(..., description="Implementation status")
    workflows: List[WorkflowStatus] = Field(default_factory=list, description="Associated workflows")


class Framework(BaseModel):
    """Mental model or framework from Model Thinker or other sources."""
    id: str = Field(..., description="Framework ID")
    name: str = Field(..., description="Framework name")
    source: str = Field(..., description="Source (e.g., 'Model Thinker', 'Kahneman', 'Algorithms to Live By')")
    category: str = Field(default="general", description="Framework category (e.g., decision_making, systems, computational)")
    description: str = Field(..., description="What this framework is")
    when_to_use: str = Field(..., description="When to apply this framework")
    key_concepts: List[str] = Field(default_factory=list, description="Core concepts")
    limitations: str = Field(default="", description="Framework limitations")
    applicable_dimensions: List[str] = Field(default_factory=list, description="Which LIAM dimensions this applies to")


class DimensionQuery(BaseModel):
    """Query for applicable dimensions."""
    problem_description: str = Field(..., description="Description of the problem/decision")
    context: Optional[str] = Field(None, description="Additional context")


class FrameworkQuery(BaseModel):
    """Query for applicable frameworks."""
    problem_description: str = Field(..., description="Description of the problem/decision")
    dimension_filter: Optional[List[str]] = Field(None, description="Filter by dimension IDs")
    limit: int = Field(default=5, ge=1, le=20)


class FrameworkRecommendation(BaseModel):
    """Framework recommendation with relevance score."""
    framework: Framework
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this framework is relevant")
    applicable_dimensions: List[str] = Field(default_factory=list)
