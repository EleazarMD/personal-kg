"""Household + contact roles — the explicit people graph (per-user).

Who the user's family is, and how outside contacts relate to them, as
typed nodes and edges instead of free-text preference rows:

    (:HouseholdMember {user_id, member_id, name, relation, aliases,
                       birth_date, grade, school})
    (:Contact {user_id, email, name})
        -[:ROLE_FOR {role, context, source, confidence, confirmed,
                     evidence, updated_at}]->(:HouseholdMember)

Why: with only preference text ("Sofia is 9/10, 4th grade", "Camp at
Groves Elementary" for Luca), an agent asked "who is this teacher's
email about?" can only guess — and guessed the wrong child. A ROLE_FOR
edge answers it deterministically.

Rules:
- Every node carries user_id and lives in the caller's own PCG graph
  (get_pic_store(uid)); the caller is the verified JWT subject.
- Contacts are keyed by lowercased email — the canonical join key to the
  CIG Person node (CIG ADR-0001), so CIG and PCG agree on who is who.
- `source="user"` edges are confirmed facts (confidence 1.0) and win.
  Derived edges (source="derived") are proposals with evidence; they
  never overwrite a confirmed edge.

Mounted at /api/pic (api.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

try:
    from .pic_routes import get_pic_store, _resolve_user_id
except ImportError:  # running as a flat module
    from pic_routes import get_pic_store, _resolve_user_id

router = APIRouter(prefix="/api/pic", tags=["Household"])

RELATIONS = {"spouse", "partner", "daughter", "son", "child", "parent", "sibling", "other"}
ROLES = {"teacher", "coach", "doctor", "school", "caregiver", "friend", "family", "other"}
SOURCES = {"user", "derived"}

_SLUG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _is_member_id(value: str) -> bool:
    """Lowercase slug: 1-64 chars of [a-z0-9_-], starting alphanumeric."""
    return (0 < len(value) <= 64 and value[0] not in "_-"
            and all(ch in _SLUG_CHARS for ch in value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(x_user_id: str, authorization: str) -> str:
    uid = _resolve_user_id(x_user_id, authorization)
    if not uid:
        raise HTTPException(status_code=401, detail="Authenticated user_id is required")
    return uid


def _email(value: str) -> str:
    email = (value or "").strip().lower()
    if "@" not in email or len(email) > 320:
        raise HTTPException(status_code=422, detail="A contact email is required")
    return email


def _driver(uid: str):
    store = get_pic_store(uid)
    with store.graph.driver.session() as session:
        session.run("CREATE CONSTRAINT household_member IF NOT EXISTS FOR (m:HouseholdMember) REQUIRE (m.user_id, m.member_id) IS UNIQUE")
        session.run("CREATE CONSTRAINT household_contact IF NOT EXISTS FOR (c:Contact) REQUIRE (c.user_id, c.email) IS UNIQUE")
    return store.graph.driver


class MemberIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    relation: str = "child"
    aliases: List[str] = []
    birth_date: Optional[str] = None  # ISO date, or "--MM-DD" when the year is unknown
    grade: Optional[str] = None
    school: Optional[str] = None


class RoleIn(BaseModel):
    member_id: str
    role: str = "other"
    context: Optional[str] = Field(None, max_length=200)  # e.g. "4th grade homeroom"
    contact_name: Optional[str] = Field(None, max_length=120)
    source: str = "user"
    confidence: float = 1.0
    evidence: List[str] = []  # message ids / event ids that support a derived edge


def _member_dict(node) -> dict:
    d = dict(node)
    d.pop("user_id", None)
    return d


@router.get("/household")
async def list_household(x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    uid = _uid(x_user_id, authorization)
    with _driver(uid).session() as s:
        rows = s.run(
            "MATCH (m:HouseholdMember {user_id: $uid}) RETURN m ORDER BY m.name", uid=uid)
        return {"members": [_member_dict(r["m"]) for r in rows]}


@router.put("/household/{member_id}")
async def upsert_member(member_id: str, body: MemberIn,
                        x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    uid = _uid(x_user_id, authorization)
    if not _is_member_id(member_id):
        raise HTTPException(status_code=422, detail="member_id must be a lowercase slug")
    if body.relation not in RELATIONS:
        raise HTTPException(status_code=422, detail=f"relation must be one of {sorted(RELATIONS)}")
    props = body.model_dump(exclude_none=True)
    props["aliases"] = sorted({a.strip() for a in body.aliases if a.strip()})
    with _driver(uid).session() as s:
        rec = s.run(
            """
            MERGE (m:HouseholdMember {user_id: $uid, member_id: $mid})
            ON CREATE SET m.created_at = $now
            SET m += $props, m.updated_at = $now
            RETURN m
            """, uid=uid, mid=member_id, props=props, now=_now()).single()
    return {"member": _member_dict(rec["m"])}


@router.get("/contacts/{email}/roles")
async def contact_roles(email: str, x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    uid = _uid(x_user_id, authorization)
    email = _email(email)
    with _driver(uid).session() as s:
        rows = s.run(
            """
            MATCH (c:Contact {user_id: $uid, email: $email})-[r:ROLE_FOR]->(m:HouseholdMember {user_id: $uid})
            RETURN c.name AS contact_name, r, m
            ORDER BY r.confirmed DESC, r.confidence DESC
            """, uid=uid, email=email)
        roles = [{
            "member_id": row["m"]["member_id"],
            "member_name": row["m"]["name"],
            "relation": row["m"].get("relation"),
            **{k: v for k, v in dict(row["r"]).items()},
        } for row in rows]
    return {"email": email, "roles": roles}


@router.put("/contacts/{email}/roles")
async def set_contact_role(email: str, body: RoleIn,
                           x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    uid = _uid(x_user_id, authorization)
    email = _email(email)
    if body.role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {sorted(ROLES)}")
    if body.source not in SOURCES:
        raise HTTPException(status_code=422, detail="source must be 'user' or 'derived'")
    confirmed = body.source == "user"
    confidence = 1.0 if confirmed else max(0.0, min(body.confidence, 0.95))
    with _driver(uid).session() as s:
        member = s.run("MATCH (m:HouseholdMember {user_id: $uid, member_id: $mid}) RETURN m",
                       uid=uid, mid=body.member_id).single()
        if not member:
            raise HTTPException(status_code=404, detail="Unknown household member")
        # A user-confirmed edge is never downgraded by a derived proposal.
        rec = s.run(
            """
            MERGE (c:Contact {user_id: $uid, email: $email})
            ON CREATE SET c.created_at = $now
            SET c.name = coalesce($cname, c.name), c.updated_at = $now
            WITH c
            MATCH (m:HouseholdMember {user_id: $uid, member_id: $mid})
            MERGE (c)-[r:ROLE_FOR]->(m)
            WITH r, coalesce(r.confirmed, false) AS was_confirmed
            SET r += CASE WHEN was_confirmed AND NOT $confirmed THEN {} ELSE {
                    role: $role, context: $ctx, source: $source, confidence: $conf,
                    confirmed: $confirmed, evidence: $evidence, updated_at: $now } END
            RETURN r
            """,
            uid=uid, email=email, mid=body.member_id, cname=body.contact_name,
            role=body.role, ctx=body.context, source=body.source, conf=confidence,
            confirmed=confirmed, evidence=body.evidence[:20], now=_now()).single()
    return {"email": email, "member_id": body.member_id, "edge": dict(rec["r"])}


@router.delete("/contacts/{email}/roles/{member_id}")
async def delete_contact_role(email: str, member_id: str,
                              x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    """The user says "not about her" — remove the edge (confirmed or not)."""
    uid = _uid(x_user_id, authorization)
    email = _email(email)
    with _driver(uid).session() as s:
        n = s.run(
            """
            MATCH (:Contact {user_id: $uid, email: $email})-[r:ROLE_FOR]->(:HouseholdMember {user_id: $uid, member_id: $mid})
            DELETE r RETURN count(r) AS n
            """, uid=uid, email=email, mid=member_id).single()["n"]
    return {"deleted": n}


@router.get("/household/{member_id}/contacts")
async def member_contacts(member_id: str, x_user_id: str = Header(default=""), authorization: str = Header(default="")):
    uid = _uid(x_user_id, authorization)
    with _driver(uid).session() as s:
        rows = s.run(
            """
            MATCH (c:Contact {user_id: $uid})-[r:ROLE_FOR]->(:HouseholdMember {user_id: $uid, member_id: $mid})
            RETURN c, r ORDER BY r.confirmed DESC, c.name
            """, uid=uid, mid=member_id)
        return {"member_id": member_id, "contacts": [
            {"email": row["c"]["email"], "name": row["c"].get("name"), **dict(row["r"])} for row in rows]}
