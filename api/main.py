"""API de la Character Factory.

Les routes suivent le §1.3 du brief. Les étages qui demandent un GPU
sont branchés sur le worker factice tant que le worker réel n'est pas
résident : l'enchaînement, la file, la progression et le stockage sont
réels, seul le calcul est simulé. C'est ce qui permet de valider le
lot 1 sans attendre les poids.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

import storage
from config import settings
from jobs import store
from llm_proxy import router as llm_router
from models import (
    Asset3D, Character, CharacterIn, Costume, CostumeIn, FaceIn, Job,
    MeshIn, OrthoViews, Rig, RigIn, Sheet, SheetIn, Timeline, ViewsIn,
)
from workers import stub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("factory")

# Entrepôt en mémoire. Il tient le temps d'une session ; la persistance
# est le sujet du lot suivant, pas du lot 1.
DB: dict[str, dict] = {"characters": {}, "costumes": {}, "sheets": {}, "views": {}, "assets": {}, "rigs": {}, "timelines": {}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.open_access:
        log.warning("FACTORY_TOKENS est vide : l'API accepte tout le monde")
    await store.start()
    yield
    await store.stop()


app = FastAPI(title="Character Factory", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── autorisation ────────────────────────────────────────────────────

async def require_token(authorization: str = Header(default="")) -> None:
    if settings.open_access:
        return
    token = authorization.removeprefix("Bearer ").strip()
    if token not in settings.tokens:
        raise HTTPException(401, "jeton absent ou inconnu")


guarded = APIRouter(dependencies=[Depends(require_token)])


# ── état du service ─────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "node": settings.node,
        "queue": "redis+rq" if settings.has_redis else "mémoire",
        "storage": "minio" if settings.has_minio else "disque local",
        "llm": bool(settings.llm_url),
        "auth": "ouvert" if settings.open_access else "jeton",
    }


# ── personnages ─────────────────────────────────────────────────────

@guarded.post("/characters", response_model=Character)
async def create_character(body: CharacterIn) -> Character:
    c = Character(**body.model_dump())
    DB["characters"][c.id] = c
    return c


@guarded.get("/characters", response_model=list[Character])
async def list_characters() -> list[Character]:
    return list(DB["characters"].values())


def _character(cid: str) -> Character:
    c = DB["characters"].get(cid)
    if c is None:
        raise HTTPException(404, "personnage inconnu")
    return c


@guarded.get("/characters/{cid}", response_model=Character)
async def get_character(cid: str) -> Character:
    return _character(cid)


@guarded.post("/characters/{cid}/face", response_model=Job)
async def generate_face(cid: str, body: FaceIn) -> Job:
    _character(cid)
    return await store.submit("face", stub.run, kind="face", width=768, height=768)


@guarded.post("/characters/{cid}/face/lock", response_model=Character)
async def lock_face(cid: str, url: str) -> Character:
    """Fige le visage. À partir d'ici il fait autorité sur l'identité
    dans toutes les générations suivantes ; le brief interdit qu'il
    change d'un costume à l'autre."""
    c = _character(cid)
    if c.face_locked_url:
        raise HTTPException(409, "le visage est déjà verrouillé pour ce personnage")
    c.face_locked_url = url
    return c


# ── costumes ────────────────────────────────────────────────────────

@guarded.post("/characters/{cid}/costumes", response_model=Costume)
async def create_costume(cid: str, body: CostumeIn) -> Costume:
    _character(cid)
    cos = Costume(character_id=cid, **body.model_dump())
    DB["costumes"][cos.id] = cos
    return cos


def _costume(cos_id: str) -> Costume:
    cos = DB["costumes"].get(cos_id)
    if cos is None:
        raise HTTPException(404, "costume inconnu")
    return cos


@guarded.get("/characters/{cid}/costumes", response_model=list[Costume])
async def list_costumes(cid: str) -> list[Costume]:
    _character(cid)
    return [c for c in DB["costumes"].values() if c.character_id == cid]


# ── planche et vues ─────────────────────────────────────────────────

@guarded.post("/costumes/{cos_id}/sheet", response_model=Job)
async def generate_sheet(cos_id: str, body: SheetIn) -> Job:
    """Planche en cinq frames. H3 est un modèle vidéo, mais rien
    n'oblige à l'utiliser comme tel : cinq frames suffisent à produire
    une planche figée, pour un coût dérisoire face à un clip."""
    _costume(cos_id)
    return await store.submit("sheet", stub.run, kind="sheet", width=1920, height=1080)


@guarded.post("/costumes/{cos_id}/views", response_model=list[Job])
async def generate_views(cos_id: str, body: ViewsIn) -> list[Job]:
    """Une génération plein cadre par azimut. La planche ne nourrit pas
    la 3D : un de ses panneaux fait 400 à 500 px de large, c'est trop
    peu pour Hunyuan3D."""
    _costume(cos_id)
    return [
        await store.submit(f"view:{int(az)}", stub.run, kind=f"view/{int(az)}", width=1024, height=1536)
        for az in body.azimuths
    ]


@guarded.post("/costumes/{cos_id}/views/check")
async def check_views(cos_id: str, angles: dict[str, float]) -> dict:
    """Passe de contrôle d'alignement. Le brief §6.2 est catégorique :
    des entrées mal alignées donnent un résultat pire qu'une seule
    image, donc on refuse explicitement au-delà de ±5°."""
    _costume(cos_id)
    expected = {"front": 0.0, "left": 90.0, "back": 180.0, "right": 270.0}
    errors = {}
    for name, target in expected.items():
        if name not in angles:
            errors[name] = "vue absente"
            continue
        delta = abs((angles[name] - target + 180.0) % 360.0 - 180.0)
        if delta > 5.0:
            errors[name] = f"écart de {delta:.1f}° — au-delà de la tolérance de 5°"
    return {"ok": not errors, "errors": errors}


# ── 3D, rig, animation ──────────────────────────────────────────────

@guarded.post("/costumes/{cos_id}/mesh", response_model=Job)
async def generate_mesh(cos_id: str, body: MeshIn) -> Job:
    _costume(cos_id)
    return await store.submit("mesh", stub.run, kind=f"mesh/{body.engine}", width=1024, height=1024)


@guarded.post("/assets/{asset_id}/rig", response_model=Job)
async def rig_asset(asset_id: str, body: RigIn) -> Job:
    """On ne modélise jamais en T-pose : le skinning calculé à 90° fait
    remonter les épaules quand on rabat les bras. Bind en A-pose, delta
    de bind stocké, conversion appliquée au retarget."""
    if body.bind_pose != "apose":
        raise HTTPException(400, "le bind se fait en A-pose ; la conversion vers la T-pose SOMA est un delta, pas un bind")
    return await store.submit("rig", stub.run, kind="rig", width=512, height=512)


@guarded.post("/takes", response_model=Job)
async def create_take(source: str = "video", body_engine: str = "kimodo") -> Job:
    return await store.submit("take", stub.run, kind=f"take/{body_engine}", width=512, height=512)


@guarded.post("/timelines/{tid}/bake", response_model=Job)
async def bake_timeline(tid: str) -> Job:
    return await store.submit("bake", stub.run, kind="bake", width=512, height=512)


# ── travaux ─────────────────────────────────────────────────────────

@guarded.get("/jobs", response_model=list[Job])
async def list_jobs() -> list[Job]:
    return store.all()


@guarded.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "travail inconnu")
    return job


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """Progression en SSE. EventSource ne sait pas poser d'en-tête, donc
    cette route n'est pas derrière le garde : l'identifiant de travail
    est déjà une donnée non devinable, et rien de sensible n'y transite."""
    return StreamingResponse(
        store.events(job_id),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no", "connection": "keep-alive"},
    )


# ── artefacts en mode local ─────────────────────────────────────────

@app.get("/artifacts/{key:path}")
async def artifact(key: str):
    path = storage.local_path(key)
    if path is None:
        raise HTTPException(404, "artefact introuvable, ou servi par MinIO")
    return FileResponse(path)


app.include_router(guarded)
app.include_router(llm_router, dependencies=[Depends(require_token)])
