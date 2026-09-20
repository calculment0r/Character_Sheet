"""Le modèle de données du brief, §2.

Règle dure reprise telle quelle : une seule identité MHR par
personnage, figée à la première validation et réutilisée partout. Si
l'identité change entre deux costumes, les longueurs d'os bougent et
tout l'aval devient incohérent — d'où le verrou sur `identity`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


Style = Literal["photoreal", "stylized"]
MeshEngine = Literal["hunyuan3d-2.1", "trellis2"]
BodyEngine = Literal["sam3dbody", "kimodo"]
BindPose = Literal["apose", "tpose"]


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Identity(BaseModel):
    """L'identité MHR. 45 paramètres de forme, 68 d'échelle — les deux
    sont requis côté SOMA quand le backend d'identité est MHR."""

    mhr_identity: list[float] = Field(default_factory=list, max_length=45)
    mhr_scales: list[float] = Field(default_factory=list, max_length=68)


class Character(BaseModel):
    id: str = Field(default_factory=lambda: _id("chr"))
    name: str
    style: Style = "photoreal"
    face_refs: list[str] = Field(default_factory=list)
    face_prompt: str = ""
    face_locked_url: str | None = None
    identity: Identity | None = None
    sheet: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class Costume(BaseModel):
    id: str = Field(default_factory=lambda: _id("cos"))
    character_id: str
    name: str
    refs: list[str] = Field(default_factory=list)
    prompt: str = ""
    status: Literal["draft", "validated"] = "draft"
    fullbody_url: str | None = None


class Panel(BaseModel):
    role: str
    azimuth: float
    elevation: float = 0.0
    url: str | None = None


class Sheet(BaseModel):
    id: str = Field(default_factory=lambda: _id("sht"))
    costume_id: str
    panels: list[Panel] = Field(default_factory=list)
    h3_job_id: str | None = None
    prompt_used: str = ""


class OrthoViews(BaseModel):
    id: str = Field(default_factory=lambda: _id("ort"))
    costume_id: str
    front_url: str | None = None
    left_url: str | None = None
    back_url: str | None = None
    right_url: str | None = None
    threequarter_url: str | None = None
    delighted: bool = False
    # Écart angulaire mesuré par la passe de contrôle, en degrés. Le
    # brief §6.2 refuse au-delà de ±5°.
    angle_error: dict[str, float] = Field(default_factory=dict)


class Asset3D(BaseModel):
    id: str = Field(default_factory=lambda: _id("a3d"))
    costume_id: str
    engine: MeshEngine = "trellis2"
    mesh_url: str | None = None
    albedo_url: str | None = None
    metallic_url: str | None = None
    roughness_url: str | None = None
    normal_url: str | None = None
    version: int = 1
    parent_version: int | None = None


class Rig(BaseModel):
    id: str = Field(default_factory=lambda: _id("rig"))
    asset3d_id: str
    skeleton: Literal["soma77"] = "soma77"
    bind_pose: BindPose = "apose"
    bind_delta_url: str | None = None
    skin_url: str | None = None


class Take(BaseModel):
    id: str = Field(default_factory=lambda: _id("tak"))
    source: Literal["video", "images", "authored"]
    body_engine: BodyEngine = "kimodo"
    npz_url: str | None = None
    fps: float = 30.0
    frame_count: int = 0


class Track(BaseModel):
    type: Literal["body", "hands_l", "hands_r", "face"]
    take_id: str
    in_: int = Field(0, alias="in")
    out: int = 0
    weight: float = 1.0
    time_warp: float = 1.0

    model_config = {"populate_by_name": True}


class Timeline(BaseModel):
    id: str = Field(default_factory=lambda: _id("tml"))
    character_id: str
    costume_id: str
    tracks: list[Track] = Field(default_factory=list)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: _id("job"))
    kind: str
    state: JobState = JobState.queued
    progress: float = 0.0
    message: str = ""
    node: str = ""
    result: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── corps de requête ────────────────────────────────────────────────

class CharacterIn(BaseModel):
    name: str
    style: Style = "photoreal"
    face_prompt: str = ""
    sheet: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class FaceIn(BaseModel):
    prompt: str = ""
    refs: list[str] = Field(default_factory=list)
    seed: int | None = None
    variants: int = 4


class CostumeIn(BaseModel):
    name: str
    prompt: str = ""
    refs: list[str] = Field(default_factory=list)


class SheetIn(BaseModel):
    prompt: dict[str, str]
    mask_face_in_fullbody: bool = False
    frames: int = 5


class ViewsIn(BaseModel):
    # Une génération par vue, ou un orbite continu qu'on redécoupe.
    method: Literal["per_view", "orbit"] = "per_view"
    azimuths: list[float] = Field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    delight: bool = True


class MeshIn(BaseModel):
    engine: MeshEngine = "trellis2"
    single_view: bool = False


class RigIn(BaseModel):
    bind_pose: BindPose = "apose"
