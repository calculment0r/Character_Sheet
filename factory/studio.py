"""`./usine studio` : la chaîne dans une page, servie depuis DGX2.

Une application, pas une suite de commandes : l'accueil montre les
personnages en cartes, un clic ouvre le personnage, et toute la
conception se fait à la main — fiche d'identité par la conversation,
visage, costumes, plein pied, planche, vues, 3D, rig. Les étages sont
ceux de `chain.py`, appelés tels quels : le studio ne fait que les
mettre en file et montrer ce qu'ils produisent.

Le serveur tourne sur la machine qui calcule (DGX2), sur le réseau
local, sans rien d'autre que la bibliothèque standard :

  /                            studio.html (accueil ; #/p/<slug> : un personnage)
  /v1/models, /v1/chat/completions
                               relais vers le modèle de texte d'Ollama
                               (`llm_url`, `llm_model`) : la console de
                               l'étage Identité le trouve seule, sur son origine
  /api/system                  mémoire, modèle de texte, ComfyUI, travail en cours
  /api/characters              GET la liste, POST une création
  /api/characters/<slug>       GET le détail ; PUT …/identity ; POST …/actions/<action>
  /api/uploads                 POST une image de référence (corps brut)
  /api/jobs[/<id>]             la file de travaux ; POST …/<id>/cancel
  /files/<slug>/<chemin>       les fichiers d'un personnage

Une seule file, un seul ouvrier : un travail GPU à la fois. Avant
chaque travail, `memory.Manager` décharge le modèle de texte et vide le
ComfyUI qui ne sert pas (DGX Spark : 128 Go partagés, saturé il gèle).
Les `print` des étages vont au journal du travail, les `report` à sa
progression.
"""

from __future__ import annotations

import http.server
import io
import json
import mimetypes
import queue
import re
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import chain, config, h3, memory
from .project import IDENTITY_SCHEMA, ChainError, Project, list_projects, now

PORT = 8765
STATIC_DIRS = ("js", "assets", "data", "docs/img", "etat")
STATIC_PAGES = ("studio.html", "console.html", "viewer.html", "theme.html", "index.html")
UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")
MAX_UPLOAD = 40 * 1024 * 1024
SHEET_FIELDS = 22
LOG_LINES = 400

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("text/markdown", ".md")


def uploads_dir() -> Path:
    return config.projects_root() / ".uploads"


# ── journal par travail ────────────────────────────────────────────

_local = threading.local()


class _Router(io.TextIOBase):
    """`sys.stdout` du studio : ce qu'un étage imprime pendant un travail
    va au journal de ce travail, le reste à la console."""

    def __init__(self, real) -> None:
        self.real = real

    def write(self, s: str) -> int:
        job = getattr(_local, "job", None)
        if job is None:
            return self.real.write(s)
        job.write(s)
        return len(s)

    def flush(self) -> None:
        self.real.flush()


class Job:
    def __init__(self, slug: str, action: str, params: dict, label: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.slug, self.action, self.params, self.label = slug, action, params, label
        self.status = "queued"          # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = "en file"
        self.lines: list[str] = []
        self._partial = ""
        self.created, self.started, self.ended = now(), None, None
        self.result = None
        self.error = None
        self.cancel = False

    def write(self, s: str) -> None:
        self._partial += s
        *done, self._partial = self._partial.split("\n")
        self.lines.extend(line.rstrip() for line in done if line.strip())
        del self.lines[:-LOG_LINES]

    def say(self, message: str) -> None:
        self.message = message
        self.lines.append(f"  · {message}")
        del self.lines[:-LOG_LINES]

    def public(self, full: bool = False) -> dict:
        return {"id": self.id, "slug": self.slug, "action": self.action, "label": self.label,
                "params": self.params, "status": self.status, "progress": round(self.progress, 3),
                "message": self.message, "created": self.created, "started": self.started, "ended": self.ended,
                "error": self.error, "result": self.result,
                "log": self.lines if full else self.lines[-10:]}


# ── les actions ────────────────────────────────────────────────────

def _int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _seed(q: dict) -> int:
    s = q.get("seed")
    return int(s) if str(s or "").strip().isdigit() else h3.new_seed()


def _uploads(ids) -> list[Path]:
    out = []
    for name in ids or []:
        if not UPLOAD_NAME.match(str(name)):
            raise ChainError(f"référence inconnue : {name}")
        path = uploads_dir() / name
        if not path.is_file():
            raise ChainError(f"référence perdue : {name} — redépose l'image")
        out.append(path)
    return out


def _costume(p: Project, q: dict) -> str:
    key, _ = p.costume(q.get("costume") or None)
    return key


def _variants(fn, n: int, base: int, report) -> list[dict]:
    """Une variante par appel, pour une progression d'ensemble lisible."""
    made = []
    for i in range(n):
        made += fn(seed=base + i, report=lambda pr, msg, i=i: report((i + pr) / n, msg))
    return made


def face_engine(p: Project | None, q: dict) -> str:
    """Le moteur du visage : celui demandé, sinon H3 s'il y a une photo
    à normaliser, sinon le modèle d'image par défaut (Z-Image Turbo)."""
    from . import portrait

    engine = q.get("engine") or ""
    if engine in portrait.ENGINES:
        return engine
    has_photo = bool(q.get("refs")) or bool(p and p.face["refs"])
    return "h3" if has_photo else config.setting("face_engine", "zimage")


def read_face_brief(p: Project, q: dict, llm, say) -> None:
    """Avant le rendu : le modèle de texte lit le brief du visage — le
    prompt d'image en sort, et ce qu'il dit de la personne va à la fiche.
    Rien à relire si le brief n'a pas bougé."""
    from . import brief

    if "brief" not in q:
        return
    # Relu à chaque tour, même inchangé : le modèle propose d'autres
    # visages, c'est ce qu'on attend d'un nouveau « Générer ».
    text = str(q["brief"] or "").strip()
    new_refs = _uploads(q.get("refs"))
    say("le modèle de texte lit le brief du visage")
    if config.backend("brief") != "stub":
        llm()
    got = brief.read_face(text, sheet=p.sheet, images=[*new_refs, *(p.path(r) for r in p.face["refs"])])
    p.face["brief"], p.face["prompt_en"], p.face["variations"] = text, got["prompt"], got.get("variants") or []
    p.data["identity"] = {**p.sheet, **got["sheet"], "character_name": p.data["name"]}
    p.save()
    say(f"visage : {got['prompt']}")


def a_face(p: Project, q: dict, report):
    if "prompt" in q:
        p.face["prompt"] = str(q["prompt"]).strip()
    refs = _uploads(q.get("refs"))
    engine = face_engine(p, q)
    if str(q.get("around") or "").isdigit():
        # « Autour de celui-ci » : la description d'un candidat, de nouvelles graines.
        cands = p.face["candidates"]
        i = int(q["around"])
        if not 1 <= i <= len(cands):
            raise ChainError(f"candidat n° {i} inexistant")
        base_cand = cands[i - 1]
        descs = [base_cand.get("desc") or p.face.get("prompt_en") or p.face["prompt"]]
        engine = q.get("engine") or base_cand.get("engine") or engine
    elif "brief" in q:
        descs = p.face.get("variations") or [p.face.get("prompt_en") or ""]
    else:
        descs = None
    first = [True]
    count = [0]

    def one(seed, report):
        r, first[0] = (refs if first[0] else []), False
        d = [descs[count[0] % len(descs)]] if descs else None
        count[0] += 1
        return chain.face(p, refs=r, variants=1, seed=seed, engine=engine, descriptions=d, report=report)

    return {"made": _variants(one, _int(q.get("variants"), 4, 1, 8), _seed(q), report)}


def a_face_lock(p: Project, q: dict, report):
    return {"locked": chain.face_lock(p, str(q.get("candidate", "")))}


def a_costume_add(p: Project, q: dict, report):
    """Un costume se décrit en mots (`brief`), avec ou sans images de
    vêtements ; son nom vient tout seul s'il n'est pas donné."""
    name = str(q.get("name") or "").strip() or f"tenue {len(p.data['costumes']) + 1}"
    key = chain.costume_add(p, name, prompt=str(q.get("prompt") or "").strip(), refs=_uploads(q.get("refs")))
    if str(q.get("brief") or "").strip():
        p.data["costumes"][key]["brief"] = str(q["brief"]).strip()
        p.save()
    return {"costume": key}


def a_costume_edit(p: Project, q: dict, report):
    key = _costume(p, q)
    cos = p.data["costumes"][key]
    if "prompt" in q:
        cos["prompt"] = str(q["prompt"]).strip()
    if "brief" in q:
        cos["brief"] = str(q["brief"] or "").strip()
    if str(q.get("name") or "").strip():
        cos["name"] = str(q["name"]).strip()
    for path in _uploads(q.get("refs")):
        cos["refs"].append(p.import_file(path, f"costumes/{key}/refs"))
    drop = set(q.get("drop_refs") or [])
    cos["refs"] = [r for r in dict.fromkeys(cos["refs"]) if r not in drop]
    p.save()
    return {"costume": key}


def read_costume_brief(p: Project, q: dict, llm, say) -> None:
    """Avant le plein pied : le brief du costume, lu par le modèle de
    texte, devient le prompt de la tenue (en anglais) et sa fiche."""
    from . import brief

    key = _costume(p, q)
    cos = p.data["costumes"][key]
    if "brief" in q:
        cos["brief"] = str(q["brief"] or "").strip()
    added = [p.import_file(path, f"costumes/{key}/refs") for path in _uploads(q.get("refs"))]
    if added:
        cos["refs"] = list(dict.fromkeys(cos["refs"] + added))
        cos["brief_read"] = None
    p.save()
    text = (cos.get("brief") or "").strip()
    if not text or (cos.get("brief_read") == text and cos.get("prompt")):
        return
    say("le modèle de texte lit le brief du costume")
    if config.backend("brief") != "stub":
        llm()
    got = brief.read_costume(text, sheet=p.sheet, images=[p.path(r) for r in cos["refs"]])
    cos["prompt"], cos["outfit"], cos["brief_read"] = got["prompt"], got["sheet"], text
    p.save()
    say(f"costume : {got['prompt']}")


def a_fullbody(p: Project, q: dict, report):
    key = _costume(p, q)
    if "prompt" in q:
        p.data["costumes"][key]["prompt"] = str(q["prompt"]).strip()
    one = lambda seed, report: chain.fullbody(p, key, variants=1, seed=seed, report=report)
    return {"made": _variants(one, _int(q.get("variants"), 2, 1, 6), _seed(q), report)}


def a_fullbody_ok(p: Project, q: dict, report):
    return {"validated": chain.fullbody_ok(p, _costume(p, q), str(q.get("candidate", "")))}


def a_sheet(p: Project, q: dict, report):
    return {"made": chain.sheet(p, _costume(p, q), mask_face=bool(q.get("mask_face")), ab=bool(q.get("ab")),
                                seed=_seed(q), report=report)}


def a_sheet_ok(p: Project, q: dict, report):
    return {"sheet": chain.sheet_ok(p, _costume(p, q), str(q.get("id", "")))["id"]}


def a_views(p: Project, q: dict, report):
    names = [n for n in (q.get("names") or []) if n in (*chain.ORTHO, "threequarter")] or None
    raw = chain.views(p, _costume(p, q), method=q.get("method") or "per_view", names=names, seed=_seed(q),
                      report=report)
    return {"views": sorted(raw)}


def a_prep(p: Project, q: dict, report):
    return {"summary": chain.prep(p, _costume(p, q), report=report)["summary"]}


def a_check(p: Project, q: dict, report):
    res = chain.check(p, _costume(p, q), measure=bool(q.get("measure")), report=report)
    return {"ok": res["ok"], "errors": res["errors"]}


def a_mesh(p: Project, q: dict, report):
    engine = q.get("engine") or None
    if engine not in (None, "trellis2", "hunyuan3d-2.1"):
        raise ChainError(f"moteur 3D inconnu : {engine}")
    e = chain.mesh(p, _costume(p, q), engine=engine, single_view=bool(q.get("single_view")), seed=_seed(q),
                   report=report)
    return {"version": e["version"], "glb": e["glb"]}


def a_rig(p: Project, q: dict, report):
    from .cli_motion import cmd_rig

    mesh = q.get("mesh")
    cmd_rig(SimpleNamespace(perso=str(p.root), costume=_costume(p, q), pose="apose",
                            mesh=int(mesh) if str(mesh or "").isdigit() else None))
    return {}


def a_rig_ok(p: Project, q: dict, report):
    from .cli_motion import cmd_rig_ok

    verdict = "accepte" if q.get("verdict") in ("accepte", "accepted", True) else "refuse"
    rig = q.get("rig")
    cmd_rig_ok(SimpleNamespace(perso=str(p.root), costume=_costume(p, q), verdict=verdict,
                               rig=int(rig) if str(rig or "").isdigit() else None))
    return {}


def _gpu_views(q: dict, p: Project | None = None):
    method = q.get("method") or "per_view"
    return ("h3", "h3") if method in ("per_view", "orbit") else ("qwen", "views")


def _gpu_face(q: dict, p: Project | None = None):
    engine = face_engine(p, q)
    return ("h3", "h3") if engine == "h3" else (engine, "portrait")


# action → (fonction, libellé, où elle calcule[, lecture préalable]).
# « Où » vaut None pour une action rapide, qui se joue tout de suite ;
# sinon (famille de modèles, capacité) — ou une fonction qui le rend. La
# lecture préalable passe par le modèle de texte avant le rendu.
ACTIONS = {
    "face":         (a_face, "variantes du visage", _gpu_face, read_face_brief),
    "face_lock":    (a_face_lock, "verrouillage du visage", None),
    "costume_add":  (a_costume_add, "nouveau costume", None),
    "costume_edit": (a_costume_edit, "costume modifié", None),
    "fullbody":     (a_fullbody, "plein pied", ("h3", "h3"), read_costume_brief),
    "fullbody_ok":  (a_fullbody_ok, "plein pied validé", None),
    "sheet":        (a_sheet, "planche", ("h3", "h3")),
    "sheet_ok":     (a_sheet_ok, "planche validée", None),
    "views":        (a_views, "vues orthogonales", _gpu_views),
    "prep":         (a_prep, "préparation des vues", ("birefnet", "prep")),
    "check":        (a_check, "contrôle d'alignement",
                     lambda q, p=None: ("sam3d", "sam3dbody") if q.get("measure") else None),
    "mesh":         (a_mesh, "mesh 3D", ("trellis", "trellis")),
    "rig":          (a_rig, "rig SOMA", ("unirig", None)),
    "rig_ok":       (a_rig_ok, "verdict du rig", None),
}

# Le moteur de chaque capacité, pour savoir si un travail touche au GPU.
_ENGINE = {"h3": "h3", "views": "h3", "prep": "prep", "trellis": "trellis", "sam3dbody": "sam3dbody",
           "portrait": "portrait"}


def where(action: str, q: dict, p: Project | None = None):
    spec = ACTIONS[action][2]
    return spec(q, p) if callable(spec) else spec


def _on_gpu(family: str, cap: str | None) -> bool:
    """Un travail n'a besoin de la mémoire que si un vrai moteur tourne :
    en factice (essais sur le PC), on ne décharge rien."""
    if family == "unirig":
        return config.backend("unirig") != "stub"
    if family == "qwen":
        return True
    return config.backend(_ENGINE.get(cap, cap)) not in ("stub", "builtin", "rembg")


# ── l'état d'un personnage ─────────────────────────────────────────

def files_url(slug: str, rel: str | None) -> str | None:
    return f"/files/{urllib.parse.quote(slug)}/{urllib.parse.quote(rel)}" if rel else None


def _next(p: Project) -> dict | None:
    """L'action suivante, celle qui prend l'orange — la même logique que
    `./usine etat`."""
    d = p.data
    face = d["face"]
    if not face.get("locked"):
        return {"action": "face_lock" if face["candidates"] else "face"}
    if not d["costumes"]:
        return {"action": "costume_add"}
    for key, cos in d["costumes"].items():
        fb = cos["fullbody"]
        if not fb.get("validated"):
            return {"action": "fullbody_ok" if fb["candidates"] else "fullbody", "costume": key}
        if not cos.get("sheet"):
            return {"action": "sheet_ok" if cos["sheets"] else "sheet", "costume": key}
        v = cos["views"]
        if not v["raw"]:
            return {"action": "views", "costume": key}
        if not v["prepared"]:
            return {"action": "prep", "costume": key}
        if not v.get("check"):
            return {"action": "check", "costume": key}
        if not cos["meshes"]:
            return {"action": "mesh", "costume": key}
        if not cos["rigs"]:
            return {"action": "rig", "costume": key}
        if cos["rigs"][-1]["verdict"] == "unseen":
            return {"action": "rig_ok", "costume": key}
    return None


def summary(p: Project) -> dict:
    d = p.data
    sheet = p.sheet
    filled = sum(1 for v in sheet.values() if str(v).strip())
    face = d["face"]
    costumes = list(d["costumes"].values())
    thumb = face.get("locked") or (face["candidates"][-1]["file"] if face["candidates"] else None)

    def any_(pred):
        return any(pred(c) for c in costumes)

    def state(done: bool, partial: bool) -> str:
        return "done" if done else "partial" if partial else "todo"

    stages = [
        ("ST-01", "identity", "Identité", state(filled >= SHEET_FIELDS, filled > 0)),
        ("ST-02", "face", "Visage", state(bool(face.get("locked")), bool(face["candidates"]))),
        ("ST-03", "costumes", "Costumes", state(bool(costumes), False)),
        ("ST-04", "fullbody", "Plein pied", state(any_(lambda c: c["fullbody"].get("validated")),
                                                  any_(lambda c: c["fullbody"]["candidates"]))),
        ("ST-05", "sheet", "Planche", state(any_(lambda c: c.get("sheet")), any_(lambda c: c["sheets"]))),
        ("ST-06", "views", "Vues", state(any_(lambda c: (c["views"].get("check") or {}).get("ok")),
                                         any_(lambda c: c["views"]["raw"]))),
        ("ST-07", "mesh", "Mesh 3D", state(any_(lambda c: c["meshes"]), False)),
        ("ST-08", "rig", "Rig", state(any_(lambda c: any(r["verdict"] == "accepted" for r in c["rigs"])),
                                      any_(lambda c: c["rigs"]))),
    ]
    return {
        "slug": d["slug"], "name": d["name"], "style": d["style"], "created_at": d.get("created_at"),
        "role": sheet.get("role", ""), "archetype": sheet.get("archetype", ""),
        "thumb": files_url(d["slug"], thumb), "locked": bool(face.get("locked")),
        "identity": {"filled": filled, "total": SHEET_FIELDS, "notes": len(d["notes"])},
        "costumes": len(costumes),
        "stages": [{"ref": r, "id": i, "label": lab, "state": st} for r, i, lab, st in stages],
        "next": _next(p),
    }


def backends() -> dict:
    return {cap: config.backend(cap) for cap in config.CAPABILITIES}


# ── le serveur ─────────────────────────────────────────────────────

class Studio:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.queue: queue.Queue[Job] = queue.Queue()
        self.running: Job | None = None
        # Le personnage d'un calcul en cours : les choix faits pendant ce
        # calcul (verrouiller, écrire le costume, corriger la fiche) passent
        # par ce même objet, sinon le calcul écraserait le manifeste.
        self.live: dict[str, Project] = {}
        self.memory = memory.Manager()
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, name="ouvrier", daemon=True).start()

    # la file

    def submit(self, slug: str, action: str, params: dict) -> Job:
        job = Job(slug, action, params, ACTIONS[action][1])
        with self.lock:
            self.jobs.append(job)
            del self.jobs[:-200]
        self.queue.put(job)
        return job

    def busy(self, slug: str) -> bool:
        return self.running is not None and self.running.slug == slug

    def project(self, slug: str) -> Project:
        return self.live.get(slug) or Project.open(slug)

    def find(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def cancel(self, job: Job) -> str:
        if job.status == "queued":
            job.status, job.message, job.ended = "cancelled", "annulé avant de partir", now()
            return "annulé"
        if job.status != "running":
            return "déjà fini"
        job.cancel = True
        spot = where(job.action, job.params)
        url = config.comfyui_url(spot[1]) if spot and spot[1] else None
        if url and _interrupt_ours(url):
            return "interruption demandée à ComfyUI"
        return "annulation demandée : l'étage s'arrêtera à sa prochaine étape"

    def _worker(self) -> None:
        while True:
            job = self.queue.get()
            if job.status != "queued":
                continue
            self.running = job
            _local.job = job
            job.status, job.started, job.message = "running", now(), "démarrage"
            try:
                self._run(job)
                job.status, job.progress = "done", 1.0
                job.message = "fini"
            except ChainError as exc:
                job.status, job.error = "error", f"refusé : {exc}"
            except MemoryError as exc:
                job.status, job.error = "error", f"mémoire : {exc}"
            except Exception as exc:  # noqa: BLE001 — tout se dit dans le journal du travail
                job.status = "error"
                job.error = "annulé" if job.cancel else f"{type(exc).__name__} : {exc}"
                job.write(traceback.format_exc())
            finally:
                job.ended = now()
                if job.error:
                    job.message = job.error
                _local.job = None
                self.live.pop(job.slug, None)
                self.running = None

    def _run(self, job: Job) -> None:
        fn, _, _, *pre = ACTIONS[job.action]
        p = self.live[job.slug] = Project.open(job.slug)
        if pre:
            pre[0](p, job.params, lambda: self.memory.before_llm(say=job.say), job.say)
        spot = where(job.action, job.params, p)
        if spot and _on_gpu(*spot):
            family, cap = spot
            self.memory.before_gpu(family, config.comfyui_url(cap) if cap else None, say=job.say)

        def report(progress: float, message: str) -> None:
            if job.cancel:
                raise ChainError("annulé")
            job.progress = max(0.0, min(1.0, float(progress)))
            job.message = message

        job.result = fn(p, job.params, report)

    # les actions rapides se jouent dans la requête, jamais sous un
    # travail en cours sur le même personnage : il tient une copie du
    # manifeste et l'écraserait en finissant.

    def act(self, slug: str, action: str, params: dict) -> dict:
        if action not in ACTIONS:
            raise ChainError(f"action inconnue : {action}")
        Project.open(slug)
        if where(action, params) is not None:
            return {"job": self.submit(slug, action, params).public()}
        p = self.project(slug)
        out = io.StringIO()
        _local.job = SimpleNamespace(write=out.write)
        try:
            with p.lock:
                result = ACTIONS[action][0](p, params, lambda pr, m: None)
        finally:
            _local.job = None
        return {"result": result, "log": out.getvalue().strip()}

    def system(self) -> dict:
        return {
            "llm": {"url": memory.ollama_url(), "model": llm_model(), "loaded": memory.llm_loaded()},
            "memory": {"available_gb": _round(memory.available_gb()), "min_free_gb": self.memory.min_free_gb},
            "comfy": {url: {"busy": memory.comfy_busy(url), "family": self.memory.family.get(url)}
                      for url in memory.comfy_instances()},
            "backends": backends(),
            "running": self.running.public() if self.running else None,
            "queued": sum(1 for j in self.jobs if j.status == "queued"),
        }


def _round(v):
    return round(v, 1) if v is not None else None


def _interrupt_ours(url: str) -> bool:
    """Interrompt le calcul en cours sur cette instance s'il vient de la
    chaîne (client `usine-…`), jamais celui d'un autre."""
    try:
        with urllib.request.urlopen(f"{url}/queue", timeout=5) as res:
            q = json.loads(res.read())
        running = q.get("queue_running") or []
        if not running or not str((running[0][3] or {}).get("client_id", "")).startswith("usine-"):
            return False
        req = urllib.request.Request(f"{url}/interrupt", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except (OSError, ValueError, IndexError, TypeError):
        return False


def llm_model() -> str:
    # qwen3-vl 32B dense, dérivé en contexte 32k (`ollama create` avec
    # PARAMETER num_ctx 32768) : au banc du 25/09, le seul des modèles de
    # DGX2 qui range la fiche par les outils à chaque tour ; le 30B-A3B
    # la récite en texte. Sans num_ctx, Ollama réserve 262k de contexte.
    return config.setting("llm_model", "qwen3-vl-32b-32k")


class Handler(http.server.BaseHTTPRequestHandler):
    studio: Studio
    server_version = "usine-studio"

    # réponses

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _error(self, code: int, message: str) -> None:
        self._json({"error": {"message": message}}, code)

    def _file(self, path: Path, cache: str = "no-cache") -> None:
        if not path.is_file():
            return self._error(404, "introuvable")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "text/javascript"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": cache})

    def _body(self, limit: int = 8 * 1024 * 1024) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ChainError(f"envoi trop gros ({n // 1024 // 1024} Mo)")
        return self.rfile.read(n) if n else b""

    def _payload(self) -> dict:
        raw = self._body()
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ChainError(f"JSON illisible : {exc}") from exc
        if not isinstance(data, dict):
            raise ChainError("un objet JSON est attendu")
        return data

    def log_message(self, fmt, *args) -> None:
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.__stderr__.write(f"  {self.address_string()} {fmt % args}\n")

    # aiguillage

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if path.startswith("/v1/"):
                return self._llm(method, path)
            if path.startswith("/api/"):
                return self._api(method, path[len("/api/"):].strip("/").split("/"))
            if method != "GET":
                return self._error(405, "méthode refusée")
            if path.startswith("/files/"):
                versioned = "v" in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self._project_file(path[len("/files/"):], versioned)
            return self._static(path)
        except ChainError as exc:
            self._error(409, str(exc))
        except MemoryError as exc:
            self._error(503, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 — la page affiche le message
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__} : {exc}")

    # fichiers

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "studio.html"
        if rel in STATIC_PAGES or any(rel.startswith(d + "/") for d in STATIC_DIRS):
            target = (config.REPO / rel).resolve()
            if target.is_relative_to(config.REPO.resolve()):
                return self._file(target)
        self._error(404, "introuvable")

    def _project_file(self, rest: str, versioned: bool = False) -> None:
        slug, _, rel = rest.partition("/")
        root = (config.projects_root() / slug).resolve()
        target = (root / rel).resolve()
        if not slug or slug.startswith(".") or not (root / "project.json").is_file() \
                or not target.is_relative_to(root):
            return self._error(404, "introuvable")
        # Une URL versionnée (?v=<date>) ne change jamais de contenu.
        self._file(target, "max-age=31536000, immutable" if versioned else "no-cache")

    # modèle de texte

    def _llm(self, method: str, path: str) -> None:
        base = memory.ollama_url()
        if path == "/v1/models" and method == "GET":
            return self._json({"object": "list", "data": [{"id": llm_model(), "object": "model",
                                                             "owned_by": "studio"}]})
        if path != "/v1/chat/completions" or method != "POST":
            return self._error(404, "le relais ne sert que /v1/models et /v1/chat/completions")
        body = self._payload()
        body["model"] = llm_model()
        body["stream"] = False
        self.studio.memory.before_chat(busy=self.studio.running is not None, say=print)
        req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as res:
                return self._send(res.status, res.read(), "application/json; charset=utf-8")
        except urllib.error.HTTPError as exc:
            return self._send(exc.code, exc.read(), "application/json; charset=utf-8")
        except OSError as exc:
            return self._error(502, f"Ollama injoignable sur {base} ({exc})")

    # API

    def _api(self, method: str, parts: list[str]) -> None:
        s = self.studio
        head = parts[0] if parts else ""
        if head == "system" and method == "GET":
            return self._json(s.system())
        if head == "uploads" and method == "POST":
            return self._json(self._upload())
        if head == "jobs":
            if len(parts) == 1 and method == "GET":
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                slug = (q.get("slug") or [None])[0]
                jobs = [j.public() for j in reversed(s.jobs) if slug in (None, j.slug)][:40]
                return self._json({"jobs": jobs, "running": s.running.id if s.running else None})
            job = s.find(parts[1]) if len(parts) > 1 else None
            if job is None:
                return self._error(404, "travail inconnu")
            if len(parts) == 2 and method == "GET":
                return self._json(job.public(full=True))
            if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                return self._json({"message": s.cancel(job), "job": job.public()})
        if head == "characters":
            if len(parts) == 1:
                if method == "GET":
                    return self._json({"characters": [summary(p) for p in _by_recent(list_projects())],
                                       "backends": backends()})
                if method == "POST":
                    return self._json(self._create(), 201)
            slug = parts[1] if len(parts) > 1 else ""
            if len(parts) == 2 and method == "GET":
                from .portrait import ENGINES

                p = s.project(slug)
                return self._json({"character": p.data, "summary": summary(p), "busy": s.busy(slug),
                                   "face_engines": ENGINES,
                                   "backends": backends(), "view_methods": list(chain.VIEW_METHODS)})
            if len(parts) == 3 and parts[2] == "identity" and method == "PUT":
                return self._json(self._identity(slug))
            if len(parts) == 4 and parts[2] == "actions" and method == "POST":
                return self._json(s.act(slug, parts[3], self._payload()))
        self._error(404, "route inconnue")

    def _upload(self) -> dict:
        from PIL import Image, UnidentifiedImageError

        raw = self._body(MAX_UPLOAD)
        try:
            img = Image.open(io.BytesIO(raw))
            img.verify()
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise ChainError(f"ce n'est pas une image lisible ({exc})") from exc
        ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(img.format)
        folder = uploads_dir()
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.{ext or 'png'}"
        if ext:
            (folder / name).write_bytes(raw)
        else:
            Image.open(io.BytesIO(raw)).convert("RGBA").save(folder / name)
        return {"id": name, "name": self.headers.get("X-Filename", name)}

    def _create(self) -> dict:
        body = self._payload()
        sheet = {k: v.strip() for k, v in (body.get("sheet") or {}).items() if isinstance(v, str)}
        name = str(body.get("name") or sheet.get("character_name") or "").strip()
        if not name:
            raise ChainError("donne un nom au personnage (le champ name de la fiche)")
        style = body.get("style") if body.get("style") in ("photoreal", "stylized") else "photoreal"
        notes = [str(n) for n in body.get("notes") or [] if str(n).strip()]
        p = Project.create(name, style=style, identity={**sheet, "character_name": sheet.get("character_name") or name},
                           notes=notes)
        if body.get("conversation"):
            p.data["identity_chat"] = _text_only(body["conversation"])
            p.save()
        return {"slug": p.data["slug"], "summary": summary(p)}

    def _identity(self, slug: str) -> dict:
        body = self._payload()
        p = self.studio.project(slug)
        with p.lock:
            return self._apply_identity(p, body)

    def _apply_identity(self, p: Project, body: dict) -> dict:
        if "sheet" in body:
            p.set_identity({"schema": IDENTITY_SCHEMA, "sheet": body.get("sheet") or {},
                            "notes": body.get("notes", p.data["notes"])})
            name = str(p.sheet.get("character_name") or "").strip()
            if name:
                p.data["name"] = name
        if str(body.get("name") or "").strip():
            # Le nom s'édite ; le dossier (slug) garde celui de la création.
            p.data["name"] = str(body["name"]).strip()
            p.data["identity"] = {**p.sheet, "character_name": p.data["name"]}
        if isinstance(body.get("fields"), dict):
            # Une correction à la main, champ par champ, depuis le panneau de la fiche.
            p.data["identity"] = {**p.sheet, **{k: str(v).strip() for k, v in body["fields"].items()
                                                if isinstance(v, (str, int, float))}}
        if body.get("style") in ("photoreal", "stylized"):
            p.data["style"] = body["style"]
        if "conversation" in body:
            p.data["identity_chat"] = _text_only(body["conversation"])
        p.save()
        return {"summary": summary(p)}


def _by_recent(projects: list[Project]) -> list[Project]:
    return sorted(projects, key=lambda p: p.data.get("created_at") or "", reverse=True)


def _text_only(conversation) -> list[dict]:
    """La conversation, sans les images (elles pèseraient des mégaoctets
    dans le manifeste) : chaque image devient une mention."""
    out = []
    for msg in conversation if isinstance(conversation, list) else []:
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = [b if b.get("type") != "image" else {"type": "text", "text": "[image de référence]"}
                       for b in content if isinstance(b, dict)]
        out.append({"role": msg["role"], "content": content})
    return out


def serve(*, host: str = "0.0.0.0", port: int = PORT) -> None:
    sys.stdout = _Router(sys.stdout)
    handler = type("StudioHandler", (Handler,), {"studio": Studio()})
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    print(f"studio : http://{host if host != '0.0.0.0' else '<cette machine>'}:{port}/")
    print(f"  personnages : {config.projects_root()}")
    print(f"  modèle de texte : {llm_model()} ({memory.ollama_url()})")
    for url in memory.comfy_instances():
        print(f"  ComfyUI : {url}")
    print("  Ctrl+C pour arrêter")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
