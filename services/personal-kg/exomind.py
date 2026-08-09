"""
PCG Exomind — Surface-aware context capsule injection.

Builds compact, surface-specific context capsules from the PCG graph and
injects them into agent system prompts. Each surface (email, calendar,
documents, research, code) gets a different projection of the user's
self-model.

Surfaces:
  - email: preferences + communication styles + contact context
  - calendar: time preferences + goals + routine patterns
  - documents: writing style + topic interests + reading preferences
  - research: analytical preferences + framework affinity + depth preferences
  - code: coding conventions + tool preferences + review style

The capsule is a structured markdown block that gets prepended to the agent
system prompt. It is NOT a retrieval-augmented generation step — it is a
fixed-size identity injection that gives the agent stable access to who
the user is.

See AGENTIFICATION_PLAN.md design principle #2: "PIC is the user's self-model."
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from graph_store import GraphStore

log = logging.getLogger(__name__)

# ─── Surface definitions ────────────────────────────────────────────

SURFACE_EMAIL = "email"
SURFACE_CALENDAR = "calendar"
SURFACE_DOCUMENTS = "documents"
SURFACE_RESEARCH = "research"
SURFACE_CODE = "code"

VALID_SURFACES = {SURFACE_EMAIL, SURFACE_CALENDAR, SURFACE_DOCUMENTS, SURFACE_RESEARCH, SURFACE_CODE}

# Which preference categories are relevant to each surface
SURFACE_CATEGORIES: Dict[str, List[str]] = {
    SURFACE_EMAIL: ["communication", "email", "tone", "formatting", "language"],
    SURFACE_CALENDAR: ["schedule", "time", "routine", "priority", "availability"],
    SURFACE_DOCUMENTS: ["writing", "format", "structure", "audience", "style"],
    SURFACE_RESEARCH: ["analysis", "depth", "framework", "methodology", "sources"],
    SURFACE_CODE: ["coding", "tools", "conventions", "review", "testing"],
}

# ─── Capsule builder ────────────────────────────────────────────────


class ExomindService:
    \"\"\"Builds surface-aware context capsules from PCG.\"\"\"

    def __init__(self, graph_store: Optional[GraphStore] = None):
        self.store = graph_store or GraphStore()

    def build_capsule(
        self,
        surface: str,
        contact_email: Optional[str] = None,
        max_preferences: int = 15,
        max_observations: int = 5,
    ) -> str:
        \"\"\"Build a context capsule for the given surface.

        Args:
            surface: One of VALID_SURFACES.
            contact_email: If provided (email surface), include contact-specific
                preferences and communication style.
            max_preferences: Max preferences to include.
            max_observations: Max recent observations to include.

        Returns:
            Markdown-formatted context capsule string.
        \"\"\"
        if surface not in VALID_SURFACES:
            log.warning("Unknown surface %s, falling back to email", surface)
            surface = SURFACE_EMAIL

        sections: List[str] = []

        # Header
        sections.append(f"## User Context ({surface} surface)")
        sections.append("")

        # Identity
        identity = self._get_identity()
        if identity:
            name = identity.get("preferred_name") or identity.get("name", "")
            if name:
                sections.append(f"**User**: {name}")
            timezone = identity.get("timezone", "")
            if timezone:
                sections.append(f"**Timezone**: {timezone}")
            sections.append("")

        # Preferences (filtered by surface-relevant categories)
        prefs = self._get_surface_preferences(surface, contact_email, max_preferences)
        if prefs:
            sections.append("### Preferences")
            for p in prefs:
                value = p.get("value", "")
                scope = p.get("scope", "global")
                scope_label = f" [{scope}]" if scope != "global" else ""
                sections.append(f"- {value}{scope_label}")
            sections.append("")

        # Communication style (email surface with contact)
        if surface == SURFACE_EMAIL and contact_email:
            style = self._get_communication_style(contact_email)
            if style:
                sections.append("### Communication Style")
                for key, val in style.items():
                    if key not in ("id", "embedding", "scope") and val:
                        sections.append(f"- **{key.replace(_,  ).title()}**: {val}")
                sections.append("")

        # Recent observations (surface-relevant)
        observations = self._get_recent_observations(surface, max_observations)
        if observations:
            sections.append("### Recent Observations")
            for obs in observations:
                text = obs.get("observation", obs.get("content", ""))
                confidence = obs.get("confidence", 0.5)
                if text:
                    sections.append(f"- {text} (conf: {confidence:.0%})")
            sections.append("")

        # Active goals (all surfaces)
        goals = self._get_active_goals()
        if goals:
            sections.append("### Active Goals")
            for g in goals:
                sections.append(f"- {g.get(description, g.get(text, ))}")
            sections.append("")

        # Footer
        sections.append("---")
        sections.append(
            "*This context is auto-generated from your Personal Context Graph. "
            "Preferences marked [consolidated] were learned from patterns. "
            "Manage at: Memory Browser sidebar or Dashboard Memory Console.*"
        )

        return "\n".join(sections)

    def _get_identity(self) -> Optional[Dict[str, Any]]:
        \"\"\"Get the user identity node.\"\"\"
        try:
            with self.store.driver.session() as session:
                result = session.run("MATCH (i:Identity) RETURN i LIMIT 1")
                record = result.single()
                return dict(record["i"]) if record else None
        except Exception as e:
            log.warning("Exomind: identity fetch failed: %s", e)
            return None

    def _get_surface_preferences(
        self,
        surface: str,
        contact_email: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        \"\"\"Get preferences relevant to the surface.\"\"\"
        categories = SURFACE_CATEGORIES.get(surface, [])
        try:
            with self.store.driver.session() as session:
                if contact_email:
                    # Include both global and contact-scoped preferences
                    result = session.run(
                        \"\"\"
                        MATCH (p:Preference)
                        WHERE (p.scope = global OR p.scope = $contact_scope)
                          AND (p.category IN $categories OR p.category IS NULL)
                        RETURN p
                        ORDER BY p.confidence DESC, p.last_reinforced_at DESC
                        LIMIT $limit
                        \"\"\",
                        contact_scope=f"contact:{contact_email}",
                        categories=categories,
                        limit=limit,
                    )
                else:
                    result = session.run(
                        \"\"\"
                        MATCH (p:Preference)
                        WHERE p.scope = global
                          AND (p.category IN $categories OR p.category IS NULL)
                        RETURN p
                        ORDER BY p.confidence DESC, p.last_reinforced_at DESC
                        LIMIT $limit
                        \"\"\",
                        categories=categories,
                        limit=limit,
                    )
                return [dict(r["p"]) for r in result]
        except Exception as e:
            log.warning("Exomind: preferences fetch failed: %s", e)
            return []

    def _get_communication_style(self, contact_email: str) -> Optional[Dict[str, Any]]:
        \"\"\"Get communication style for a specific contact.\"\"\"
        try:
            return self.store.get_communication_style(f"contact:{contact_email}")
        except Exception as e:
            log.warning("Exomind: comm style fetch failed: %s", e)
            return None

    def _get_recent_observations(
        self,
        surface: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        \"\"\"Get recent observations relevant to the surface.\"\"\"
        categories = SURFACE_CATEGORIES.get(surface, [])
        try:
            with self.store.driver.session() as session:
                result = session.run(
                    \"\"\"
                    MATCH (o:Observation)
                    WHERE o.status IN [new, promoted]
                      AND (o.category IN $categories OR o.category IS NULL)
                    RETURN o
                    ORDER BY o.created_at DESC
                    LIMIT $limit
                    \"\"\",
                    categories=categories,
                    limit=limit,
                )
                return [dict(r["o"]) for r in result]
        except Exception as e:
            log.warning("Exomind: observations fetch failed: %s", e)
            return []

    def _get_active_goals(self) -> List[Dict[str, Any]]:
        \"\"\"Get active user goals.\"\"\"
        try:
            with self.store.driver.session() as session:
                result = session.run(
                    \"\"\"
                    MATCH (g:Goal)
                    WHERE g.status IS NULL OR g.status = active
                    RETURN g
                    ORDER BY g.created_at DESC
                    LIMIT 5
                    \"\"\"
                )
                return [dict(r["g"]) for r in result]
        except Exception as e:
            log.warning("Exomind: goals fetch failed: %s", e)
            return []


# ─── Module singleton ───────────────────────────────────────────────

_service: Optional[ExomindService] = None


def get_exomind_service() -> ExomindService:
    global _service
    if _service is None:
        _service = ExomindService()
    return _service
