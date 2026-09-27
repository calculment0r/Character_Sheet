"""La planche de présentation : le livrable qu'on montre.

Cal, 27/09 : « une planche hyper belle de notre character dans des
positions naturelles, des expressions etc. ». Pas une image générée d'un
coup : chaque case est une édition Qwen-Image 2.1 séparée, contrôlée, et
la planche se compose en code (`presentation_sheet.py`), façon planche
de modèle (docs/ETUDES.md §5) :

  expressions  le visage verrouillé coupé au-dessus du col (`pose.head_only`)
               en <image1>, une expression décrite par ses muscles ; sortie
               1024², encodeur à 1056 ;
  poses        le plein pied validé, en pose naturelle, en <image1>, un
               squelette OpenPose en <image2> posé sur les os du personnage
               (`tools/remote/natural_skeleton.py`, `data/natural_poses.json`) ;
               1152 × 2048, le cadre du plein pied ;
  détails      des recadrages du plein pied (col, poche, main, chaussures),
               rien d'inventé, agrandis par SeedVR2 (`upscale.py`) ;
  palette      k-means dans Lab sur le personnage détouré ;
  taille       la hauteur de la fiche (`chain.height_m`) contre une
               silhouette de référence de 1,75 m.

Chaque visage généré passe par un contrôle d'identité (FaceNet VGGFace2,
`tools/remote/face_identity.py`) : cosinus avec le visage verrouillé,
relance sur la graine suivante sous le seuil, la meilleure gardée.
Mesures du 27/09 sur `essai-atelier` : le même visage retouché 0,95 à
0,97, un 3/4 0,85, un autre visage du même type 0,54 à 0,67.

Aucun étage technique n'en dépend et elle n'attend que le visage
verrouillé et le plein pied validé : l'A-pose, les vues et le mesh sont
pour la machine.

Manifeste : `cos["presentation"]` = {panels: {expressions, poses,
details: [{id, label, file, cut, seed, score, tries, secs, at}]},
palette: [hex], sheet, variants: {clair, sombre}, at}. Fichiers sous
`costumes/<clé>/presentation/`.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from PIL import Image

from . import config, imaging
from .project import ChainError, Project, now

EXPR_SIZE = (1024, 1024)
POSE_SIZE = (1152, 2048)
RESOLUTION = 1056           # 1024 brouille les éditions (ComfyUI #16435)
EXPR_MIN, POSE_MIN = 0.80, 0.75
# Une expression forte déplace les traits que FaceNet lit : mesuré le 27/09,
# une colère juste (même personne à l'œil) sort à 0,59-0,70, une surprise à
# 0,84 ; les autres à 0,91-0,95. Seuil par expression.
EXPR_FLOOR = {"colere": 0.65, "surprise": 0.75}
EXPR_TRIES, POSE_TRIES = 3, 2

SKELETON_SCRIPT = config.REPO / "tools/remote/natural_skeleton.py"
IDENTITY_SCRIPT = config.REPO / "tools/remote/face_identity.py"
POSES_FILE = config.REPO / "data/natural_poses.json"

# Six expressions, décrites par les muscles du visage : « joyeux » seul
# rend un sourire poli, les muscles donnent l'expression.
EXPRESSIONS = [
    ("neutre", "neutre",
     "a calm neutral expression: relaxed brow, lips gently closed, a soft steady gaze into the camera"),
    ("joie", "joie",
     "a broad open-mouth smile showing the upper teeth, cheeks raised, eyes slightly squinted with fine creases at "
     "their outer corners"),
    ("colere", "colère",
     "anger: eyebrows pulled down and together with deep vertical furrows between them, eyes narrowed in a hard "
     "glare, nostrils flared, lips pressed tight, jaw clenched"),
    ("tristesse", "tristesse",
     "sadness: the inner ends of the eyebrows raised and drawn together, upper eyelids drooping, gaze lowered, the "
     "corners of the mouth pulled down, the chin slightly puckered"),
    ("surprise", "surprise",
     "surprise: eyebrows raised high and arched, forehead wrinkled, eyes wide open with white showing above the "
     "irises, jaw dropped and the mouth open in a relaxed oval"),
    ("malice", "malice",
     "mischief: a lopsided closed-mouth smirk with one corner of the mouth raised, one eyebrow slightly lifted, "
     "eyes narrowed with a knowing sideways glance"),
]

DETAILS = [("col", "col"), ("poche", "poche"), ("main", "main"), ("chaussures", "chaussures")]

GROUPS = ("expressions", "poses", "details")


def natural_poses() -> list[dict]:
    return json.loads(POSES_FILE.read_text(encoding="utf-8"))["poses"]


# ── les prompts ────────────────────────────────────────────────────

def text_expression(emotion: str) -> str:
    return (f"Change only the facial expression of the person in <image1> to {emotion}. Keep the exact same face, "
            "identity, skin, hair, hairstyle, head angle, framing, lighting and plain light-grey background. "
            "Head-and-shoulders portrait, front view.")


def text_pose(hint: str, style: str = "photoreal") -> str:
    from .pose import LOOK, STYLIZED

    return " ".join([
        "Change the pose of the character in <image1> to match the pose in <image2>. Preserve the character's "
        "identity, clothing, appearance, and art style from <image1>.",
        f"The person {hint}.",
        "The skeleton only guides the pose; the image shows the person alone, without lines or dots.",
        "The whole figure from the top of the head to the soles of the shoes is inside the frame, at the same size "
        "as in <image1>.",
        LOOK if style == "photoreal" else STYLIZED,
    ])


# ── l'étage ────────────────────────────────────────────────────────

def state_of(cos: dict) -> dict:
    st = cos.setdefault("presentation", {})
    st.setdefault("panels", {})
    for g in GROUPS:
        st["panels"].setdefault(g, [])
    st.setdefault("palette", [])
    st.setdefault("sheet", None)
    st.setdefault("variants", {})
    return st


def _wanted(st: dict, redo: list[str] | None) -> dict[str, set[str]]:
    """Les cases à faire : celles qui manquent, ou celles de `redo` — des
    identifiants de case (`joie`, `marche`, `chaussures`), des groupes
    (`expressions`, `poses`, `details`), `all`, ou `sheet` pour ne
    refaire que la composition."""
    ids = {"expressions": [e[0] for e in EXPRESSIONS], "poses": [p["id"] for p in natural_poses()],
           "details": [d[0] for d in DETAILS]}
    if redo:
        out = {g: set() for g in GROUPS}
        for r in redo:
            r = str(r).strip()
            if r in ("all", "tout"):
                return {g: set(v) for g, v in ids.items()}
            if r in ("sheet", "planche"):
                continue
            if r in ids:
                out[r] |= set(ids[r])
                continue
            group = next((g for g, v in ids.items() if r in v), None)
            if group is None:
                every = ", ".join(i for v in ids.values() for i in v)
                raise ChainError(f"case inconnue : {r} (possibles : {every}, ou expressions, poses, details, all)")
            out[group].add(r)
        return out
    have = {g: {e["id"] for e in st["panels"][g] if e.get("file")} for g in GROUPS}
    return {g: set(v) - have[g] for g, v in ids.items()}


def run(p: Project, costume: str | None, *, redo: list[str] | None = None, seed: int | None = None,
        theme: str | None = None, report=None) -> dict:
    from . import presentation_sheet
    from .chain import _report, _seed, height_m

    locked = p.require_face()
    key, cos = p.costume(costume)
    body = p.require_fullbody(cos)
    report = report or _report()
    st = state_of(cos)
    todo = _wanted(st, redo)
    folder = p.dir(f"costumes/{key}/presentation")
    base = _seed(seed)
    started = time.monotonic()
    work = {"expressions": len(todo["expressions"]) * 1.0, "poses": len(todo["poses"]) * 1.4,
            "details": len(todo["details"]) * 0.3}
    total = sum(work.values()) + 0.6
    span = {}
    at = 0.0
    for g in GROUPS:
        span[g] = (at / total, (at + work[g]) / total)
        at += work[g]

    def part(g):
        a, b = span[g]
        return lambda pr, msg: report(a + (b - a) * max(0.0, min(1.0, pr)), msg)

    if todo["expressions"]:
        _expressions(p, st, folder, locked, sorted(todo["expressions"], key=[e[0] for e in EXPRESSIONS].index),
                     base, part("expressions"))
    natural = folder / "skeletons" / "natural.json"
    if todo["poses"] or (todo["details"] and not natural.exists()):
        order = [x["id"] for x in natural_poses()]
        _poses(p, st, folder, p.path(body), sorted(todo["poses"], key=order.index), base + 100, part("poses"))
    if todo["details"]:
        _details(p, st, folder, p.path(body), sorted(todo["details"], key=[d[0] for d in DETAILS].index),
                 base + 200, part("details"))
    report((total - 0.6) / total, "palette et composition")
    cut = folder / "fullbody_cut.png"
    if not cut.exists() or st.get("fullbody") != body or "fullbody_face" not in st:
        _matte(p.path(body), cut, folder)
        st["fullbody"] = body
        # Le visage du plein pied : l'étalon de taille des poses sur la planche.
        st["fullbody_face"] = identity(p.path(locked), [p.path(body)])[0].get("box")
    palette = presentation_sheet.palette(Image.open(cut))
    st["palette"] = [c["hex"] for c in palette]
    st["palette_share"] = [c["share"] for c in palette]
    sheets = compose(p, key, theme=theme)
    st["sheet"] = p.rel(sheets[0])
    st["variants"].update({t: p.rel(f) for t, f in zip(_themes(theme), sheets)})
    st["at"] = now()
    st["secs"] = round(time.monotonic() - started, 1)
    p.save()
    report(1.0, "planche composée")
    return st


def _themes(theme: str | None) -> list[str]:
    from .presentation_sheet import DEFAULT_THEME, THEMES

    if theme in (None, ""):
        return [DEFAULT_THEME]
    if theme in ("both", "les deux"):
        return [DEFAULT_THEME] + [t for t in THEMES if t != DEFAULT_THEME]
    if theme not in THEMES:
        raise ChainError(f"thème de planche inconnu : {theme} (possibles : {', '.join(THEMES)}, both)")
    return [theme]


def compose(p: Project, key: str, *, theme: str | None = None) -> list[Path]:
    from . import presentation_sheet

    out = []
    for t in _themes(theme):
        dest = p.path(f"costumes/{key}/presentation/sheet{'' if t == presentation_sheet.DEFAULT_THEME else '_' + t}.png")
        presentation_sheet.compose(p, key, dest, theme=t)
        out.append(dest)
    return out


def _replace(entries: list[dict], entry: dict) -> None:
    for i, e in enumerate(entries):
        if e["id"] == entry["id"]:
            entries[i] = entry
            return
    entries.append(entry)


def _stub() -> bool:
    return config.backend("portrait") == "stub"


# ── expressions ────────────────────────────────────────────────────

def _expressions(p: Project, st: dict, folder: Path, locked: str, ids: list[str], base: int, report) -> None:
    from . import pose, qwen21
    from . import stubs as sketches
    from .chain import identity_seed

    head = pose.head_only(p.path(locked), folder / "face_ref.png")
    labels = {e[0]: (e[1], e[2]) for e in EXPRESSIONS}
    tries: dict[str, list[dict]] = {i: [] for i in ids}
    pending = list(ids)
    rounds = 1 if _stub() else EXPR_TRIES
    for k in range(rounds):
        for n, eid in enumerate(pending):
            s = base + 10 * [e[0] for e in EXPRESSIONS].index(eid) + k
            dest = folder / f".expr_{eid}_{k}.png"
            text = text_expression(labels[eid][1])
            print(f"  expression {eid} · essai {k + 1} · graine {s}")
            t0 = time.monotonic()
            qwen21.generate(prompt=text, refs=[head], dest=dest, seed=s, size=EXPR_SIZE, resolution=RESOLUTION,
                            report=lambda pr, m, n=n: report((k + (n + pr) / len(pending)) / rounds, m),
                            stub=lambda s=s: sketches.portrait(EXPR_SIZE, seed=identity_seed(p), label=f"FACTICE · {eid}"))
            tries[eid].append({"file": dest, "seed": s, "secs": round(time.monotonic() - t0, 1), "prompt": text})
        scores = identity(p.path(locked), [tries[e][-1]["file"] for e in pending])
        for eid, sc in zip(pending, scores):
            tries[eid][-1].update(sc)
            if sc["score"] is not None:
                print(f"    {eid} : identité {sc['score']:.3f}")
        # Relancé sous le seuil, ou sans visage trouvé ; pas si le contrôle ne tourne pas.
        pending = [e for e in pending if tries[e][-1].get("checked")
                   and (tries[e][-1].get("score") or 0.0) < EXPR_FLOOR.get(e, EXPR_MIN)]
        if not pending:
            break
    for eid in ids:
        entry = _keep(p, folder, f"expr_{eid}", tries[eid], eid, labels[eid][0], EXPR_FLOOR.get(eid, EXPR_MIN))
        entry["prompt"] = tries[eid][0]["prompt"]
        _replace(st["panels"]["expressions"], entry)
    p.save()
    for eid in ids:
        e = next(x for x in st["panels"]["expressions"] if x["id"] == eid)
        _matte(p.path(e["file"]), p.path(e["file"]).with_name(f"expr_{eid}_cut.png"), folder)
        e["cut"] = p.rel(p.path(e["file"]).with_name(f"expr_{eid}_cut.png"))
    p.save()


def _keep(p: Project, folder: Path, stem: str, tries: list[dict], pid: str, label: str, floor: float) -> dict:
    """Garde l'essai au meilleur score d'identité, range les autres."""
    best = max(tries, key=lambda t: t.get("score") if t.get("score") is not None else -1.0)
    dest = folder / f"{stem}.png"
    Path(best["file"]).replace(dest)
    for t in tries:
        if Path(t["file"]).exists():
            Path(t["file"]).unlink()
    entry = {"id": pid, "label": label, "file": p.rel(dest), "seed": best["seed"], "score": best.get("score"),
             "eyes": best.get("eyes"), "box": best.get("box"), "secs": round(sum(t["secs"] for t in tries), 1),
             "tries": [{"seed": t["seed"], "score": t.get("score"), "secs": t["secs"]} for t in tries],
             "backend": config.backend("portrait"), "at": now()}
    if best.get("score") is not None and best["score"] < floor:
        entry["below_floor"] = True
        print(f"    {pid} : aucun essai au-dessus de {floor:.2f} — le meilleur ({best['score']:.3f}) est gardé")
    return entry


# ── poses ──────────────────────────────────────────────────────────

def _poses(p: Project, st: dict, folder: Path, body: Path, ids: list[str], base: int, report) -> None:
    from . import qwen21
    from . import stubs as sketches
    from .chain import identity_seed

    specs = {x["id"]: x for x in natural_poses()}
    skel_dir = folder / "skeletons"
    skeletons(body, skel_dir, ids or list(specs), report=lambda pr, m: report(0.02, m))
    if not ids:
        return
    locked = p.require_face()
    tries: dict[str, list[dict]] = {i: [] for i in ids}
    pending = list(ids)
    rounds = 1 if _stub() else POSE_TRIES
    order = list(specs)
    for k in range(rounds):
        for n, pid in enumerate(pending):
            s = base + 10 * order.index(pid) + k
            dest = folder / f".pose_{pid}_{k}.png"
            text = text_pose(specs[pid]["hint"], p.data["style"])
            print(f"  pose {pid} · essai {k + 1} · graine {s}")
            t0 = time.monotonic()
            qwen21.generate(prompt=text, refs=[body, skel_dir / f"{pid}.png"], dest=dest, seed=s, size=POSE_SIZE,
                            resolution=RESOLUTION,
                            report=lambda pr, m, n=n: report(0.05 + 0.9 * (k + (n + pr) / len(pending)) / rounds, m),
                            stub=lambda pid=pid: sketches.mannequin(POSE_SIZE, azimuth=float(specs[pid].get("yaw", 0)),
                                                                    seed=identity_seed(p), label=f"FACTICE · {pid}"))
            tries[pid].append({"file": dest, "seed": s, "secs": round(time.monotonic() - t0, 1), "prompt": text})
        scores = identity(p.path(locked), [tries[x][-1]["file"] for x in pending])
        for pid, sc in zip(pending, scores):
            tries[pid][-1].update(sc)
            if sc["score"] is not None:
                print(f"    {pid} : identité {sc['score']:.3f}")
        # Un visage tourné ou de dos se lit mal : sans visage trouvé, on ne relance pas.
        pending = [x for x in pending if tries[x][-1].get("score") is not None and tries[x][-1]["score"] < POSE_MIN]
        if not pending:
            break
    for pid in ids:
        entry = _keep(p, folder, f"pose_{pid}", tries[pid], pid, specs[pid]["label"], POSE_MIN)
        entry.update(prompt=tries[pid][0]["prompt"], skeleton=p.rel(skel_dir / f"{pid}.png"),
                     yaw=specs[pid].get("yaw", 0))
        _replace(st["panels"]["poses"], entry)
    p.save()
    report(0.97, "détourage des poses")
    for pid in ids:
        e = next(x for x in st["panels"]["poses"] if x["id"] == pid)
        cut = p.path(e["file"]).with_name(f"pose_{pid}_cut.png")
        _matte(p.path(e["file"]), cut, folder)
        e["cut"] = p.rel(cut)
    p.save()


def skeletons(body: Path, folder: Path, ids: list[str], report=lambda p, m: None) -> dict[str, Path]:
    """Les squelettes des poses naturelles, posés sur les os du plein pied,
    et `natural.json` (la détection brute, pour les détails)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {i: folder / f"{i}.png" for i in ids}
    if _stub():
        from .pose import _stub_skeleton

        report(0.1, "factice · squelettes")
        for i, path in out.items():
            _stub_skeleton(0).resize(POSE_SIZE).save(path)
            path.with_suffix(".json").write_text(json.dumps({"width": POSE_SIZE[0], "height": POSE_SIZE[1],
                                                             "scale": 1.0}))
        return out
    python = Path(config.setting("python_pose", "~/comfyui-env/bin/python")).expanduser()
    cmd = [str(python), str(SKELETON_SCRIPT), str(body), str(folder), str(POSE_SIZE[0]), str(POSE_SIZE[1]),
           "--poses=" + ",".join(ids)]
    report(0.05, "DWPose · squelettes des poses")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    missing = [p for p in out.values() if not p.exists()]
    if res.returncode or missing:
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise ChainError("les squelettes des poses n'ont pas pu être posés (DWPose) : " + " / ".join(tail))
    return out


# ── détails ────────────────────────────────────────────────────────

def detail_boxes(body: Path, natural: Path | None) -> dict[str, tuple[int, int, int, int]]:
    """Les recadrages carrés du plein pied, lus sur son squelette DWPose :
    le col (capuche, fermeture), la poche et la taille, la main droite, les
    chaussures. Sans squelette (factice), des fractions de la silhouette."""
    import numpy as np

    img = Image.open(body)
    w, h = img.size

    def square(cx, cy, side):
        side = int(min(side, w, h))
        x0 = int(min(max(cx - side / 2, 0), w - side))
        y0 = int(min(max(cy - side / 2, 0), h - side))
        return x0, y0, x0 + side, y0 + side

    if natural and natural.exists():
        nat = json.loads(natural.read_text(encoding="utf-8"))
        b = [None if q is None else np.array(q) for q in nat["body"]]
        sw = float(nat.get("shoulder_px") or np.linalg.norm(b[5] - b[2]))
        neck, pelvis = b[1], (b[8] + b[11]) / 2
        hand_pts = [np.array(q) for q in (nat["hands"].get("R") or []) if q is not None] or [b[4]]
        hand = np.mean(hand_pts, axis=0)
        ankles = (b[10] + b[13]) / 2
        feet_w = abs(b[10][0] - b[13][0])
        return {
            "col": square(neck[0], neck[1] + 0.22 * sw, 0.95 * sw),
            "poche": square(neck[0] * 0.3 + pelvis[0] * 0.7, pelvis[1] - 0.12 * sw, 1.0 * sw),
            "main": square(hand[0], hand[1], 0.6 * sw),
            "chaussures": square(ankles[0], ankles[1] + 0.18 * sw, max(feet_w + 0.75 * sw, 0.8 * sw)),
        }
    x0, y0, x1, y1 = imaging.matte_builtin(img.convert("RGB")).getchannel("A").getbbox() or (0, 0, w, h)
    bw, bh = x1 - x0, y1 - y0
    at = lambda fx, fy, fs: square(x0 + fx * bw, y0 + fy * bh, fs * bh)  # noqa: E731
    return {"col": at(0.5, 0.2, 0.18), "poche": at(0.5, 0.45, 0.2), "main": at(0.25, 0.5, 0.12),
            "chaussures": at(0.5, 0.94, 0.2)}


def _details(p: Project, st: dict, folder: Path, body: Path, ids: list[str], base: int, report) -> None:
    from . import upscale

    boxes = detail_boxes(body, folder / "skeletons" / "natural.json")
    labels = dict(DETAILS)
    img = Image.open(body).convert("RGB")
    for n, did in enumerate(ids):
        report(n / len(ids), f"détail {did}")
        crop = img.crop(boxes[did])
        dest = folder / f"detail_{did}.png"
        t0 = time.monotonic()
        engine = upscale.upscale(crop, dest, factor=4, seed=base + n, workdir=folder / ".upscale")
        entry = {"id": did, "label": labels[did], "file": p.rel(dest), "box": list(boxes[did]), "seed": base + n,
                 "engine": engine, "secs": round(time.monotonic() - t0, 1), "at": now()}
        print(f"  détail {did} · {engine} · {entry['secs']} s")
        _replace(st["panels"]["details"], entry)
        p.save()


# ── contrôles ──────────────────────────────────────────────────────

def identity(reference: Path, images: list[Path]) -> list[dict]:
    """Le score d'identité de chaque image contre le visage verrouillé
    (cosinus FaceNet), avec la boîte et les yeux ; `score` vaut None en
    factice, sans visage trouvé, ou si le contrôle ne tourne pas."""
    if _stub() or not images:
        return [{"score": None} for _ in images]
    python = Path(config.setting("python_pose", "~/comfyui-env/bin/python")).expanduser()
    res = subprocess.run([str(python), str(IDENTITY_SCRIPT), str(reference), *map(str, images)],
                         capture_output=True, text=True, timeout=600)
    try:
        got = json.loads(res.stdout.strip().splitlines()[-1])["images"]
    except (IndexError, ValueError, KeyError):
        tail = (res.stderr or res.stdout).strip().splitlines()[-2:]
        print("    contrôle d'identité indisponible : " + " / ".join(tail))
        return [{"score": None} for _ in images]
    return [{"score": g["score"], "eyes": g.get("eyes"), "box": g.get("box"), "checked": True} for g in got]


def _matte(src: Path, dest: Path, folder: Path) -> Path:
    img = imaging.load(src).convert("RGB")
    imaging.matte(img, config.backend("prep"), workdir=folder / ".matte").save(dest)
    return dest
