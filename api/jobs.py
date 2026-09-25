"""File de travaux et flux d'événements.

Deux régimes, une seule interface :

  - Sans Redis, le travail tourne dans un fil de ce processus. C'est
    le mode de mise au point : ça démarre sans rien installer, et ça
    perd tout au redémarrage.
  - Avec Redis, le travail part dans une file RQ que les workers GPU
    consomment, et la progression revient par un canal pubsub. C'est
    le régime décrit au §1.1 du brief, et le seul qui tienne à deux
    DGX : la file est partagée, les workers portent une étiquette de
    nœud, et rien d'autre n'est local au worker que le cache de poids.

La progression sort en SSE, jamais en scrutation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from config import settings
from models import Job, JobState

log = logging.getLogger("factory.jobs")

CHANNEL = "factory:events"


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="factory")
        self._redis = None
        self._rq = None
        self._bridge: asyncio.Task | None = None

    # ── cycle de vie ───────────────────────────────────────────────

    async def start(self) -> None:
        if not settings.has_redis:
            log.warning("pas de Redis : file en mémoire, tout est perdu au redémarrage")
            return
        try:
            import redis.asyncio as aioredis
            from redis import Redis
            from rq import Queue

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
            self._rq = Queue("factory", connection=Redis.from_url(settings.redis_url))
            self._bridge = asyncio.create_task(self._listen())
            log.info("Redis en place, file RQ « factory »")
        except Exception as exc:  # noqa: BLE001 — on dégrade, on ne meurt pas
            log.error("Redis injoignable (%s) : repli sur la file en mémoire", exc)
            self._redis = None
            self._rq = None

    async def stop(self) -> None:
        if self._bridge:
            self._bridge.cancel()
        self._pool.shutdown(wait=False)
        if self._redis:
            await self._redis.aclose()

    async def _listen(self) -> None:
        """Rapatrie dans ce processus les événements publiés par les
        workers, où qu'ils tournent."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        async for raw in pubsub.listen():
            if raw.get("type") != "message":
                continue
            try:
                payload = json.loads(raw["data"])
            except (ValueError, TypeError):
                continue
            job_id = payload.get("id")
            if not job_id:
                continue
            job = self._jobs.get(job_id)
            if job:
                for key in ("state", "progress", "message", "node", "result", "error"):
                    if key in payload:
                        setattr(job, key, payload[key])
                job.updated_at = datetime.now(timezone.utc)
            self._fanout(job_id, payload)

    # ── diffusion ──────────────────────────────────────────────────

    def _fanout(self, job_id: str, payload: dict) -> None:
        for q in list(self._subs.get(job_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Un abonné qui ne lit pas assez vite perd des pas
                # intermédiaires ; il recevra l'état final, qui suffit.
                pass

    async def _publish(self, job: Job, **changes) -> None:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(timezone.utc)
        payload = {
            "id": job.id, "kind": job.kind, "state": job.state.value,
            "progress": job.progress, "message": job.message,
            "node": job.node, "result": job.result, "error": job.error,
        }
        if self._redis:
            await self._redis.publish(CHANNEL, json.dumps(payload))
        else:
            self._fanout(job.id, payload)

    # ── soumission ─────────────────────────────────────────────────

    async def submit(self, kind: str, fn: Callable, /, **kwargs) -> Job:
        job = Job(kind=kind, node=settings.node)
        self._jobs[job.id] = job

        if self._rq is not None:
            # En production le worker GPU prend la main ; il publiera sa
            # propre progression sur le canal.
            self._rq.enqueue(fn, job_id=job.id, **kwargs, job_timeout=3600)
            await self._publish(job, state=JobState.queued, message="en file")
            return job

        asyncio.create_task(self._run_local(job, fn, kwargs))
        return job

    async def _run_local(self, job: Job, fn: Callable, kwargs: dict) -> None:
        loop = asyncio.get_running_loop()

        def report(progress: float, message: str) -> None:
            # Appelé depuis le fil de travail : on repasse par la boucle.
            asyncio.run_coroutine_threadsafe(
                self._publish(job, state=JobState.running, progress=progress, message=message),
                loop,
            )

        await self._publish(job, state=JobState.running, progress=0.0, message="démarrage")
        try:
            result = await loop.run_in_executor(
                self._pool, lambda: fn(report=report, job_id=job.id, **kwargs)
            )
            await self._publish(job, state=JobState.done, progress=1.0, message="terminé", result=result or {})
        except Exception as exc:  # noqa: BLE001 — l'échec est une donnée, pas un crash
            log.exception("travail %s en échec", job.id)
            await self._publish(job, state=JobState.failed, message="échec", error=str(exc))

    # ── lecture ────────────────────────────────────────────────────

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def events(self, job_id: str) -> AsyncIterator[str]:
        """Flux SSE d'un travail. Le premier message est l'état courant,
        pour qu'un client qui arrive en retard ne reste pas aveugle."""
        job = self._jobs.get(job_id)
        if job is None:
            yield _sse({"id": job_id, "state": "unknown", "error": "travail inconnu"})
            return

        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subs.setdefault(job_id, set()).add(q)
        try:
            yield _sse({
                "id": job.id, "kind": job.kind, "state": job.state.value,
                "progress": job.progress, "message": job.message,
                "node": job.node, "result": job.result, "error": job.error,
            })
            if job.state in (JobState.done, JobState.failed, JobState.cancelled):
                return

            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    # Un battement régulier : les tunnels coupent les
                    # connexions silencieuses.
                    yield ": ping\n\n"
                    continue
                yield _sse(payload)
                if payload.get("state") in ("done", "failed", "cancelled"):
                    return
        finally:
            subs = self._subs.get(job_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subs.pop(job_id, None)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


store = JobStore()
