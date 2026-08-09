"""
PCG v2 routes — primary namespace `/api/pcg/*`.

Covers the Phase A5 endpoints from AGENTIFICATION_PLAN.md:
  - POST /api/pcg/communication-style
  - GET  /api/pcg/communication-style?contact=<email>
  - GET  /api/pcg/communication-styles
  - POST /api/pcg/workflow-run            (create / start)
  - PATCH /api/pcg/workflow-run/{run_id}  (terminal update)
  - GET  /api/pcg/contact-context?email=<addr>   ← the email-personalization endpoint
  - GET  /api/pcg/insights/recent
  - GET  /api/pcg/insights/by-date/{date}

The same APIRouter is mounted at `/api/pic` in api.py as a deprecated alias
so existing Nova / Hermes / OpenClaw callers keep working. See
AGENTIFICATION_PLAN.md for the terminology note (PIC is legacy).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from graph_store import GraphStore
from models import (
    CommunicationStyle,
    CommunicationStyleCreate,
    ContactContextResponse,
    EmailDraft,
    EmailDraftCreate,
    EmailDraftStatus,
    EmailDraftUpdate,
    Insight,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from embeddings import get_embedder


# ---------------------------------------------------------------------------
# Store lazy init — share the graph driver with pic_routes by going through
# the same GraphStore constructor. We take a reference in api.py startup.
# ---------------------------------------------------------------------------

_graph: Optional[GraphStore] = None


def set_graph_store(store: GraphStore) -> None:
    """Called from api.py lifespan after graph_store.connect()."""
    global _graph
    _graph = store


def _store() -> GraphStore:
    if _graph is None:
        raise HTTPException(
            status_code=503,
            detail="PCG graph store not initialized",
        )
    return _graph


# Build the router WITHOUT a prefix; api.py mounts it twice (once at
# /api/pcg, once at /api/pic) so both namespaces reach the same handlers.
router = APIRouter(tags=["PCG"])


# ---------------------------------------------------------------------------
# CommunicationStyle
# ---------------------------------------------------------------------------


@router.post("/communication-style")
async def create_communication_style(data: CommunicationStyleCreate):
    """Create or update a communication style. MERGE keyed on `scope`, so
    repeat calls for the same scope update in place. Embeds `description`
    via the AI Gateway so it's searchable via Neo4j vector index.
    """
    description_text = data.description or " ".join(
        filter(None, [data.tone, data.length, data.greeting, data.signoff])
    )
    embedding = None
    if description_text.strip():
        try:
            embedding = await get_embedder().embed(description_text)
        except Exception:  # noqa: BLE001 — never fail the write on embed error
            embedding = None

    style = CommunicationStyle(
        id=f"style_{uuid.uuid4().hex[:12]}",
        scope=data.scope,
        tone=data.tone,
        length=data.length,
        greeting=data.greeting,
        signoff=data.signoff,
        description=data.description,
        confidence=data.confidence,
        source=data.source,
        embedding=embedding,
    )
    saved = _store().upsert_communication_style(style)

    # Auto-link to the CIG Person if scope is contact-scoped.
    linked = False
    if data.scope.startswith("contact:"):
        email = data.scope[len("contact:") :]
        try:
            linked = _store().link_style_to_person(saved.id, email)
        except Exception:  # noqa: BLE001
            linked = False

    return {
        "status": "saved",
        "style": saved.model_dump(),
        "linked_to_person": linked,
        "embedded": embedding is not None,
    }


@router.get("/communication-style")
async def get_communication_style(
    contact: Optional[str] = Query(None, description="Contact email for per-contact style"),
    scope: Optional[str] = Query(None, description="Explicit scope (overrides contact)"),
):
    """Fetch a single style by scope. If `contact` is provided it is
    translated to `scope='contact:<email>'`. If neither is provided,
    returns the global style.
    """
    resolved_scope = scope or (f"contact:{contact}" if contact else "global")
    style = _store().get_communication_style(resolved_scope)
    if not style:
        return {"scope": resolved_scope, "style": None}
    return {"scope": resolved_scope, "style": style}


@router.get("/communication-styles")
async def list_communication_styles(
    scope_prefix: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List styles, optionally filtered by scope prefix ('contact:',
    'topic:', or exact 'global')."""
    styles = _store().list_communication_styles(
        scope_prefix=scope_prefix, limit=limit
    )
    return {"styles": styles, "count": len(styles)}


# ---------------------------------------------------------------------------
# WorkflowRun audit
# ---------------------------------------------------------------------------


@router.post("/workflow-run")
async def create_workflow_run(data: WorkflowRunCreate):
    """Register a workflow-run audit node. Called from Hatchet activities
    at run start. Idempotent on `run_id` via MERGE."""
    run = WorkflowRun(
        run_id=data.run_id,
        workflow_name=data.workflow_name,
        status=data.status,
        triggered_by=data.triggered_by,
        dry_run=data.dry_run,
        inputs_summary=data.inputs_summary,
        metadata=data.metadata,
    )
    saved = _store().upsert_workflow_run(run)
    return {"status": "registered", "workflow_run": saved.model_dump()}


@router.patch("/workflow-run/{run_id}")
async def update_workflow_run(run_id: str, data: WorkflowRunUpdate):
    """Terminal-state update (succeeded / failed / cancelled). Called by
    Hatchet activities at run completion."""
    updated = _store().update_workflow_run_terminal(
        run_id=run_id,
        status=data.status,
        outputs_summary=data.outputs_summary,
        error=data.error,
        metadata=data.metadata,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"WorkflowRun {run_id} not found",
        )
    return {"status": "updated", "run_id": run_id, "new_status": data.status.value}


# ---------------------------------------------------------------------------
# Contact context — the email-personalization endpoint
# ---------------------------------------------------------------------------


@router.get("/contact-context", response_model=ContactContextResponse)
async def get_contact_context(
    email: str = Query(..., description="Contact email address"),
    email_limit: int = Query(10, ge=1, le=50),
    observation_limit: int = Query(10, ge=1, le=50),
    topic_limit: int = Query(5, ge=1, le=20),
):
    """Single-traversal join of PCG user-side (Identity + scoped Preferences
    + CommunicationStyle + Observations) and CIG world-side (Person + recent
    Emails + Topics) for a contact. Powers Hermes email personalization in
    Phase B2."""
    data = _store().get_contact_context(
        contact_email=email,
        email_limit=email_limit,
        observation_limit=observation_limit,
        topic_limit=topic_limit,
    )
    return ContactContextResponse(**data)


# ---------------------------------------------------------------------------
# Insights (daily-consolidation output, Phase C)
# ---------------------------------------------------------------------------


@router.get("/insights/recent")
async def list_recent_insights(limit: int = Query(10, ge=1, le=100)):
    """Most recent daily-consolidation insights. Used by Nova skill
    `pcg_insights` (Phase C3) and any dashboard surface."""
    insights = _store().list_recent_insights(limit=limit)
    return {"insights": insights, "count": len(insights)}


@router.get("/insights/by-date/{date}")
async def list_insights_by_date(date: str):
    """All insights generated on a given ISO date (YYYY-MM-DD)."""
    # Light validation.
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    insights = _store().list_insights_by_date(date=date)
    return {"date": date, "insights": insights, "count": len(insights)}


# ---------------------------------------------------------------------------
# EmailDraft — PiCode-reviewable draft lifecycle (Phase B2a)
# ---------------------------------------------------------------------------


@router.post("/drafts")
async def create_draft(data: EmailDraftCreate):
    """Create a new email draft. Called by the Hermes draft step of the
    email-respond-with-approval workflow. Initial status is 'generated';
    the workflow will flip to 'pending_review' once it's ready for human
    review (typically immediately)."""
    draft = EmailDraft(
        id=f"draft_{uuid.uuid4().hex[:12]}",
        run_id=data.run_id,
        in_reply_to_email_id=data.in_reply_to_email_id,
        to_email=data.to_email,
        to_name=data.to_name,
        cc_emails=data.cc_emails,
        bcc_emails=data.bcc_emails,
        subject=data.subject,
        body=data.body,
        status=EmailDraftStatus.GENERATED,
        drafter_agent=data.drafter_agent,
        review_channel=data.review_channel,
        scheduled_for=data.scheduled_for,
        metadata=data.metadata,
    )
    saved = _store().create_email_draft(draft)
    return {"status": "created", "draft": saved.model_dump()}


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str):
    draft = _store().get_email_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return {"draft": draft}


@router.get("/drafts")
async def list_drafts(
    status: Optional[str] = Query(
        None,
        description="Filter by draft status (generated / pending_review / "
        "approved / scheduled / sent / cancelled)",
    ),
    review_channel: Optional[str] = Query(
        None, description="Filter by review surface (picode / ios / voice)"
    ),
    limit: int = Query(50, ge=1, le=200),
):
    """List drafts. PiCode's review inbox queries this with
    `status=pending_review&review_channel=picode`."""
    drafts = _store().list_email_drafts(
        status=status, review_channel=review_channel, limit=limit
    )
    return {"drafts": drafts, "count": len(drafts)}


@router.patch("/drafts/{draft_id}")
async def update_draft(draft_id: str, data: EmailDraftUpdate):
    """Patch a draft. Used by:
      - PiCode / iOS when the user edits body/subject or marks status=approved
      - The workflow when it attaches an approval_id or transitions to sent
    """
    updates = {k: v for k, v in data.model_dump().items() if k != "is_user_edit" and v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    updated = _store().update_email_draft(
        draft_id=draft_id,
        updates=updates,
        is_user_edit=data.is_user_edit,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return {"status": "updated", "draft": updated}


# =========================================================================
# Phase C: Consolidation + Exomind (Dreaming + context capsule)
# =========================================================================

from consolidation import get_consolidation_service
from exomind import get_exomind_service, VALID_SURFACES


@router.post("/consolidate")
async def trigger_consolidation(lookback_hours: int = Query(24, ge=1, le=168)):
    \"\"\"Manually trigger the consolidation (Dreaming) pipeline.
    Returns summary stats from the run.\"\"\"
    service = get_consolidation_service()
    stats = await service.run(lookback_hours=lookback_hours)
    return stats


@router.get("/exomind/capsule")
async def get_context_capsule(
    surface: str = Query("email", description=f"Surface: {, .join(VALID_SURFACES)}"),
    contact: Optional[str] = Query(None, description="Contact email (email surface only)"),
    max_prefs: int = Query(15, ge=1, le=50),
    max_obs: int = Query(5, ge=0, le=20),
):
    \"\"\"Build a surface-aware context capsule for agent system prompt injection.
    Returns markdown-formatted context block.\"\"\"
    service = get_exomind_service()
    capsule = service.build_capsule(
        surface=surface,
        contact_email=contact,
        max_preferences=max_prefs,
        max_observations=max_obs,
    )
    return {"surface": surface, "capsule": capsule}


# =========================================================================
# Intervention Bridge — IDE → PCG sync
# =========================================================================
# The IDE's IWorkflowMemoryService stores interventions locally in
# IStorageService. This endpoint accepts intervention records from the IDE
# so they can be surfaced in the Dashboard Memory Console and Hyperspace iOS.
# PCG stores them as :Observation nodes with category='intervention'
# and links them to the originating :WorkflowRun if available.

from pydantic import BaseModel, Field
from typing import Optional, List


class InterventionBridgeInput(BaseModel):
    interventionId: str
    traceId: Optional[str] = None
    turnId: Optional[str] = None
    workflowId: str
    ts: float
    origin: str = "user_override"
    agentOutput: dict
    userFix: dict = {}
    tag: str
    note: Optional[str] = None
    modelId: Optional[str] = None
    harnessProfile: Optional[str] = None


class InterventionBridgeBatch(BaseModel):
    interventions: List[InterventionBridgeInput]


@router.post("/interventions/sync")
async def sync_interventions(batch: InterventionBridgeBatch):
    """Receive a batch of intervention records from the IDE.
    Stores each as an :Observation with category='intervention' and
    links to :WorkflowRun if the workflow_id matches an existing run."""
    store = _store()
    synced = 0
    errors = []
    for rec in batch.interventions:
        try:
            obs_text = f"[{rec.tag}] {rec.agentOutput.get('kind', 'unknown')}: {rec.note or rec.agentOutput.get('excerpt', '')}"
            obs = store.record_observation(
                observation=obs_text,
                category="intervention",
                source="ide_bridge",
                user_id="local",
            )
            # Add structured fields
            obs_id = obs.get('id')
            if obs_id:
                with store.driver.session() as session:
                    session.run(
                        \"\"\"
                        MATCH (o:Observation {id: $id})
                        SET o.intervention_id = $intervention_id,
                            o.tag = $tag,
                            o.origin = $origin,
                            o.workflow_id = $workflow_id,
                            o.model_id = $model_id,
                            o.harness_profile = $harness_profile,
                            o.confidence = 1.0
                        \"\"\",
                        id=obs_id,
                        intervention_id=rec.interventionId,
                        tag=rec.tag,
                        origin=rec.origin,
                        workflow_id=rec.workflowId,
                        model_id=rec.modelId,
                        harness_profile=rec.harnessProfile,
                    )
            synced += 1
        except Exception as e:
            errors.append(f"{rec.interventionId}: {e}")
    return {"synced": synced, "errors": errors}


@router.get("/interventions")
async def list_interventions(
    since_ts: Optional[float] = Query(None, description="Unix timestamp filter"),
    tag: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List intervention records stored via the bridge."""
    store = _store()
    with store.driver.session() as session:
        if since_ts and tag:
            result = session.run(
                \"\"\"
                MATCH (o:Observation)
                WHERE o.category = 'intervention'
                  AND o.created_at >= datetime($since)
                  AND o.tag = $tag
                RETURN o
                ORDER BY o.created_at DESC
                LIMIT $limit
                \"\"\",
                since=datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat(),
                tag=tag,
                limit=limit,
            )
        elif since_ts:
            result = session.run(
                \"\"\"
                MATCH (o:Observation)
                WHERE o.category = 'intervention'
                  AND o.created_at >= datetime($since)
                RETURN o
                ORDER BY o.created_at DESC
                LIMIT $limit
                \"\"\",
                since=datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat(),
                limit=limit,
            )
        else:
            result = session.run(
                \"\"\"
                MATCH (o:Observation)
                WHERE o.category = 'intervention'
                RETURN o
                ORDER BY o.created_at DESC
                LIMIT $limit
                \"\"\",
                limit=limit,
            )
        return {"interventions": [dict(r["o"]) for r in result]}
