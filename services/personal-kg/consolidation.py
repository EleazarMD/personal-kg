"""
PCG Consolidation Service — the "Dreaming" pipeline.

Runs daily (6am UTC cron) or on-demand via POST /api/pcg/consolidate.

Pipeline:
  1. gather-recent-observations (last 24h, status='new')
  2. cluster (embedding similarity + LLM label)
  3. for-each-cluster: { summarize → create Insight → link :CONSOLIDATES }
  4. promote (clusters with size≥3 and mean confidence≥0.7)
     → upsert Preference/CommunicationStyle + :PROMOTED_TO edge
  5. decay (observations older than 30d with status='new' → 'decayed')
  6. record-workflow-run

See AGENTIFICATION_PLAN.md Phase C1.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import get_settings
from embeddings import get_embedder
from graph_store import GraphStore
from models import Insight, InsightType, WorkflowRun, WorkflowRunStatus

log = logging.getLogger(__name__)

# ─── Clustering ─────────────────────────────────────────────────────


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def cluster_observations(
    observations: List[Dict[str, Any]],
    embedder=None,
    similarity_threshold: float = 0.55,
) -> List[List[Dict[str, Any]]]:
    """Cluster observations by semantic similarity.

    Tries embedding-based clustering first (if embedder is available and
    healthy). Falls back to LLM-based clustering if embeddings fail.
    As a last resort, groups by category.
    """
    # Try embedding-based clustering
    if embedder and embedder.is_known_healthy:
        try:
            obs_texts = []
            obs_to_embed = []
            for obs in observations:
                if not obs.get("embedding"):
                    text = obs.get("observation", obs.get("content", str(obs)))
                    obs_texts.append(text)
                    obs_to_embed.append(obs)

            if obs_to_embed:
                embeddings = await embedder.embed_batch(obs_texts)
                for obs, emb in zip(obs_to_embed, embeddings):
                    obs["embedding"] = emb

            clusters: List[List[Dict[str, Any]]] = []
            for obs in observations:
                obs_vec = obs.get("embedding")
                if not obs_vec:
                    clusters.append([obs])
                    continue
                best_cluster_idx = -1
                best_sim = 0.0
                for i, cluster in enumerate(clusters):
                    for member in cluster:
                        member_vec = member.get("embedding")
                        if not member_vec:
                            continue
                        sim = _cosine_similarity(obs_vec, member_vec)
                        if sim > best_sim:
                            best_sim = sim
                            best_cluster_idx = i
                if best_sim >= similarity_threshold and best_cluster_idx >= 0:
                    clusters[best_cluster_idx].append(obs)
                else:
                    clusters.append([obs])
            if len(clusters) < len(observations):
                log.info("Embedding clustering: %d obs -> %d clusters", len(observations), len(clusters))
                return clusters
        except Exception as e:
            log.warning("Embedding clustering failed (%s), falling back to category grouping", e)

    # Fallback: group by category (observations have a 'category' field)
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for obs in observations:
        cat = obs.get("category", "other") or "other"
        by_cat.setdefault(cat, []).append(obs)
    clusters = list(by_cat.values())
    log.info("Category clustering: %d obs -> %d clusters (by category)", len(observations), len(clusters))
    return clusters


# ─── LLM Summarization ──────────────────────────────────────────────


async def _summarize_cluster(
    observations: List[Dict[str, Any]],
    settings=None,
) -> Tuple[str, float]:
    """Use the AI Gateway to summarize a cluster of observations into an
    insight. Returns (summary_text, confidence).

    Confidence is derived from cluster size and LLM self-reported confidence.
    """
    settings = settings or get_settings()
    gateway_url = settings.ai_gateway_url.rstrip("/")
    # AI Gateway v2 uses /api/chat/completions (not /v1/chat/completions)
    if gateway_url.endswith("/v1"):
        gateway_url = gateway_url[: -len("/v1")]
    if not gateway_url.endswith("/api"):
        gateway_url = gateway_url + "/api"
    model = settings.consolidation_model or "qwen3-30b-a3b"

    obs_texts = []
    for o in observations:
        text = o.get("observation", o.get("content", str(o)))
        # If the observation is a JSON blob, extract the human-readable value
        if isinstance(text, str) and text.strip().startswith("{"):
            try:
                import json as _json
                parsed = _json.loads(text)
                text = parsed.get("value", parsed.get("observation", parsed.get("text", text)))
            except (ValueError, TypeError):
                pass
        obs_texts.append(str(text))
    prompt = (
        "You are a personal context consolidation agent. Below are recent "
        "observations about a user. Summarize them into a single concise "
        "insight (1-2 sentences). Then rate your confidence (0.0-1.0) that "
        "this represents a durable preference or pattern.\\n\\n"
        f"Observations ({len(observations)}):\\n"
        + "\\n".join(f"- {t}" for t in obs_texts)
        + "\\n\\nRespond in format:\\nINSIGHT: <text>\\nCONFIDENCE: <0.0-1.0>"
    )

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                f"{gateway_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.ai_gateway_api_key or 'ai-gateway-api-key-2024'}",
                    "X-API-Key": settings.ai_gateway_api_key or 'ai-gateway-api-key-2024',
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.warning("consolidation LLM HTTP %d: %s", resp.status, text[:200])
                    return ("", 0.0)
                data = await resp.json()
                content = data["choices"][0]["message"].get("content") or ""
                # Strip Qwen3 thinking tokens — the model generates a
                # thinking block even with enable_thinking=false (GPTQ-int4
                # quantization on DGX-spark doesn't reliably respect it).
                # The actual answer is after the </thinking_398284847> marker.
                if "</thinking_398284847>" in content:
                    content = content.split("</thinking_398284847>")[-1].strip()
                return _parse_llm_response(content)
    except Exception as e:
        log.warning("consolidation LLM failed: %s", e)
        return _fallback_summary(observations)


def _parse_llm_response(content: str) -> Tuple[str, float]:
    """Parse INSIGHT/CONFIDENCE format."""
    # Strip Qwen3 thinking tokens. The model generates a thinking block
    # (starting with  "  "  "  "  "  ") even with enable_thinking=false.
    # The actual answer (INSIGHT: ... CONFIDENCE: ...) comes after the
    # thinking block. If there's no explicit  marker, look for the
    # INSIGHT: line which marks the start of the real answer.
    if "INSIGHT:" in content:
        # Find the INSIGHT: line and take everything from there
        idx = content.upper().find("INSIGHT:")
        if idx > 0:
            content = content[idx:].strip()
    insight = ""
    confidence = 0.5
    for line in content.strip().split("\\n"):
        if line.upper().startswith("INSIGHT:"):
            insight = line[len("INSIGHT:"):].strip()
        elif line.upper().startswith("CONFIDENCE:"):
            try:
                confidence = float(line[len("CONFIDENCE:"):].strip())
            except ValueError:
                pass
    if not insight:
        if not content:
            return ("", 0.0)
        insight = content.strip().split("\\n")[0]
    return insight, max(0.0, min(1.0, confidence))


def _fallback_summary(observations: List[Dict[str, Any]]) -> Tuple[str, float]:
    """Simple concatenation fallback when LLM is unavailable."""
    texts = [o.get("observation", o.get("content", "")) for o in observations]
    summary = f"Pattern across {len(observations)} observations: " + "; ".join(texts[:3])
    return summary[:300], 0.4


# ─── Consolidation Orchestrator ─────────────────────────────────────


class ConsolidationService:
    """Orchestrates the daily consolidation pipeline."""

    def __init__(self, graph_store: Optional[GraphStore] = None, settings=None):
        self.store = graph_store or GraphStore()
        if not self.store.driver:
            self.store.connect()
        self.settings = settings or get_settings()
        self.embedder = get_embedder()

    async def run(self, lookback_hours: int = 24, user_id: str = "") -> Dict[str, Any]:
        """Run the full consolidation pipeline. Returns summary stats."""
        started_at = datetime.now(timezone.utc)
        stats = {
            "started_at": started_at.isoformat(),
        "user_id": user_id,
            "observations_gathered": 0,
            "clusters_formed": 0,
            "insights_created": 0,
            "promotions": 0,
            "decayed": 0,
            "errors": [],
        }

        try:
            # 1. Gather recent observations
            since = (started_at - timedelta(hours=lookback_hours)).isoformat()
            observations = self.store.list_observations_since(since, status="new", limit=500, user_id=user_id)
            stats["observations_gathered"] = len(observations)

            if not observations:
                log.info("Consolidation: no new observations in last %dh", lookback_hours)
                stats["finished_at"] = datetime.now(timezone.utc).isoformat()
                return stats

            # 2. Cluster
            clusters = await cluster_observations(observations, embedder=self.embedder, similarity_threshold=0.55)
            stats["clusters_formed"] = len(clusters)

            # 3. Summarize each cluster → create Insight
            for cluster in clusters:
                # Process all clusters, including singletons. A single
                # observation can still yield a useful insight — the LLM
                # summarizer will decide if it's a durable pattern.
                # Only skip if the observation text is empty.
                if not any(o.get("observation") or o.get("content") for o in cluster):
                    continue

                summary, confidence = await _summarize_cluster(cluster, self.settings)
                insight_id = f"insight_{uuid.uuid4().hex[:12]}"

                # Embed the insight
                # Embed the insight (may fail if embeddings unavailable)
                try:
                    embedding = await self.embedder.embed(summary)
                except Exception:
                    embedding = None

                # Dedup: skip if similar insight exists for same date
                try:
                    with self.store.driver.session() as dedup_s:
                        existing = dedup_s.run(
                            'MATCH (i:Insight {date: \\}) WHERE i.title CONTAINS \\ OR \\ CONTAINS i.title RETURN count(i) as c',
                            date=started_at.strftime('%Y-%m-%d'),
                            frag=summary[:40],
                        ).single()
                        if existing and existing['c'] > 0:
                            log.info('Consolidation: skipping duplicate insight (title=%s)', summary[:60])
                            continue
                except Exception:
                    pass

                insight = Insight(
                    id=insight_id,
                    date=started_at.strftime("%Y-%m-%d"),
                    title=summary[:80],
                    summary=summary,
                    confidence=confidence,
                    cluster_size=len(cluster),
                    insight_type=InsightType.PATTERN,
                    embedding=embedding,
                )

                self.store.create_insight(insight)
                stats["insights_created"] += 1

                # Link insight to observations via :CONSOLIDATES
                for obs in cluster:
                    obs_id = obs.get("id")
                    if obs_id:
                        try:
                            self.store.link_consolidates(insight_id, obs_id)
                        except Exception as e:
                            log.warning("link_consolidates failed: %s", e)

                # 4. Promote high-confidence clusters
                if len(cluster) >= 2 and confidence >= 0.5:
                    try:
                        self._promote_cluster(cluster, summary, confidence, user_id=user_id)
                        stats["promotions"] += 1
                    except Exception as e:
                        log.warning("Promotion failed: %s", e)
                        stats["errors"].append(f"promotion: {e}")

            # 5. Decay stale observations (older than 30 days)
            cutoff = (started_at - timedelta(days=30)).isoformat()
            decayed = self.store.decay_stale_observations(cutoff, user_id=user_id)
            stats["decayed"] = decayed

        except Exception as e:
            log.error("Consolidation failed: %s", e)
            stats["errors"].append(str(e))

        # 6. Record workflow run
        finished_at = datetime.now(timezone.utc)
        stats["finished_at"] = finished_at.isoformat()
        try:
            import json as _json
            run = WorkflowRun(
                run_id=f"consolidation_{started_at.strftime('%Y%m%d_%H%M%S')}",
                workflow_name="pcg.daily-consolidation",
                started_at=started_at,
                completed_at=finished_at,
                status=WorkflowRunStatus.SUCCEEDED,
                outputs_summary=_json.dumps(stats, default=str),
                triggered_by="cron",
                metadata={"pipeline": "consolidation"},
            )
            self.store.upsert_workflow_run(run)
        except Exception as e:
            log.warning("Failed to record workflow run: %s", e)

        log.info(
            "Consolidation complete: %d obs → %d clusters → %d insights, %d promotions, %d decayed",
            stats["observations_gathered"],
            stats["clusters_formed"],
            stats["insights_created"],
            stats["promotions"],
            stats["decayed"],
        )

        # Notify via Hyperspace webhook → APNs push
        if stats["insights_created"] > 0 or stats["decayed"] > 0:
            await self._notify_consolidation(stats)

        return stats

    async def _notify_consolidation(self, stats: dict) -> None:
        """Fire-and-forget webhook to Hyperspace for APNs push notification."""
        import aiohttp
        webhook_url = self.settings.hyperspace_url.rstrip("/") + "/api/memory/push/webhook"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                await session.post(webhook_url, json={
                    "event_type": "consolidation_ready",
                    "user_id": "local",
                    "data": {
                        "insight_count": stats["insights_created"],
                        "promotion_count": stats["promotions"],
                        "decayed_count": stats["decayed"],
                    },
                })
        except Exception as e:
            log.debug("Consolidation notification failed (non-blocking): %s", e)

    def _promote_cluster(
        self,
        cluster: List[Dict[str, Any]],
        summary: str,
        confidence: float,
        user_id: str = "",
    ) -> None:
        """Promote a high-confidence cluster to a Preference."""
        # Extract category from first observation
        first = cluster[0]
        category = first.get("category", "general")
        key = f"consolidated_{category}_{uuid.uuid4().hex[:6]}"

        # Create preference
        pref = self.store.upsert_preference(
            user_id=user_id,
            category=category,
            key=key,
            value=summary,
            scope="global",
            source="consolidated",
            confidence=confidence,
        )

        # Link observations to the new preference via :PROMOTED_TO
        pref_id = pref.get("id")
        if pref_id:
            for obs in cluster:
                obs_id = obs.get("id")
                if obs_id:
                    self.store.link_observation_promoted_to(obs_id, "Preference", pref_id)
                    # Mark observation as promoted
                    self.store.mark_observation_promoted(obs_id)

        log.info("Promoted cluster (%d obs) → Preference %s", len(cluster), pref_id)


# ─── Module singleton ───────────────────────────────────────────────

_service: Optional[ConsolidationService] = None


def get_consolidation_service() -> ConsolidationService:
    global _service
    if _service is None:
        _service = ConsolidationService()
    return _service
