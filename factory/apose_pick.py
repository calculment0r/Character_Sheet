"""Choisir une A-pose par la mesure, sans la montrer à Cal.

Décision de Cal (27/09) : les étages techniques ne passent plus devant
lui ; la machine les valide en mesurant, et ne l'appelle qu'après deux
échecs. Pour l'A-pose, chaque proposition est relevée par DWPose (le
même relevé que le squelette, `tools/remote/pose_measure.py`) et notée :

  bras       angle épaule → poignet avec la verticale, chaque bras, près
             de 45° (le squelette les met à 45°) ; coude tendu ;
  jambes     hanche → cheville à peine ouvertes vers l'extérieur,
             chevilles plus écartées que les hanches sans grand écart ;
  de face    les deux yeux vus, épaules larges par rapport au torse ;
  cadre      tout le corps dans l'image, du sommet du crâne (estimé
             au-dessus du nez, cheveux compris) aux pieds (sous les chevilles), mains
             comprises ;
  une seule  personne dans l'image ;
  tenue      la couleur, région par région (torse, cuisses, jambes,
             bras, tête) placées par les points des deux relevés,
             comparée à celle du plein pied validé : histogrammes Lab,
             intersection. Une A-pose qui change de tenue ou de
             personne descend ici.

Une proposition qui passe tous les seuils est recevable ; la meilleure
note l'emporte. Les seuils sont réglés sur les A-poses réelles
d'`essai-atelier` (27/09) ; ils sont rangés avec chaque mesure.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import config
from .project import ChainError

SCRIPT = config.REPO / "tools/remote/pose_measure.py"

ARM_TARGET = 45.0
ARM_TOL = 12.0          # |angle − 45°| par bras
ARM_SPREAD = 12.0       # écart gauche/droite
ELBOW_BEND = 30.0       # coude : angle entre bras et avant-bras
LEG_RANGE = (-3.0, 15.0)  # hanche → cheville, vers l'extérieur
ANKLE_RATIO = (0.8, 2.6)  # écart des chevilles / écart des hanches
SHOULDER_RATIO = 0.45   # largeur d'épaules / longueur du torse : de face
FACING_REL = 0.85       # … et au moins 85 % de celle du plein pied
NOSE_OFFSET = 0.12      # nez / milieu des épaules, en carrures
FRAME_MARGIN = 0.005    # du cadre, en fraction de l'image
OUTFIT_MIN = 0.55       # similarité moyenne de la tenue
REGION_MIN = 0.35       # aucune région en dessous
MIN_SCORE = 0.3         # confiance d'un point DWPose

THRESHOLDS = {"arm_target": ARM_TARGET, "arm_tol": ARM_TOL, "arm_spread": ARM_SPREAD, "elbow_bend": ELBOW_BEND,
              "leg_range": LEG_RANGE, "ankle_ratio": ANKLE_RATIO, "shoulder_ratio": SHOULDER_RATIO,
              "facing_rel": FACING_REL, "nose_offset": NOSE_OFFSET, "frame_margin": FRAME_MARGIN, "outfit_min": OUTFIT_MIN, "region_min": REGION_MIN}

# OpenPose 18.
NOSE, NECK, RSH, REL, RWR, LSH, LEL, LWR, RHIP, RKNE, RANK, LHIP, LKNE, LANK, REYE, LEYE = range(16)
NEEDED = (NOSE, NECK, RSH, REL, RWR, LSH, LEL, LWR, RHIP, RKNE, RANK, LHIP, LKNE, LANK)


# ── relevé ─────────────────────────────────────────────────────────

def _stub_fail(stage: str) -> bool:
    return stage in os.getenv("FACTORY_STUB_FAIL", "").split(",")


def measure(images: list[Path], *, workdir: Path, report=lambda p, m: None) -> dict[str, dict]:
    """Le relevé DWPose de chaque image, par chemin (str). En factice : les
    points exacts du mannequin (`stubs.mannequin_keypoints`)."""
    images = [Path(i) for i in images]
    if config.backend("portrait") == "stub":
        from . import stubs

        out = {}
        for path in images:
            size = Image.open(path).size
            # FACTORY_STUB_FAIL=apose : les propositions laissent pendre les bras.
            down = _stub_fail("apose") and path.parent.name == "apose"
            out[str(path)] = {"width": size[0], "height": size[1], "people": 1,
                              "body": stubs.mannequin_keypoints(size, arms_down=down)}
        return out
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / "pose_measure.json"
    python = Path(config.setting("python_pose", "~/comfyui-env/bin/python")).expanduser()
    report(0.1, f"DWPose · relevé de {len(images)} image(s)")
    res = subprocess.run([str(python), str(SCRIPT), str(dest), *map(str, images)], capture_output=True, text=True,
                         timeout=600)
    if res.returncode or not dest.exists():
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise ChainError("le relevé DWPose a échoué : " + " / ".join(tail))
    return json.loads(dest.read_text(encoding="utf-8"))


# ── note ───────────────────────────────────────────────────────────

def _pts(entry: dict) -> list[np.ndarray | None]:
    body = entry.get("body") or []
    return [None if (p is None or p[2] < MIN_SCORE) else np.array(p[:2], float) for p in body] + [None] * (18 - len(body))


def _down_angle(a: np.ndarray, b: np.ndarray) -> float:
    """Angle du segment a → b avec la verticale descendante, en degrés."""
    d = b - a
    return math.degrees(math.atan2(abs(d[0]), d[1])) if d[1] > 0 else 180.0 - math.degrees(math.atan2(abs(d[0]), -d[1]))


def _outward(a: np.ndarray, b: np.ndarray, side: int) -> float:
    """Angle signé hanche → cheville avec la verticale, positif vers
    l'extérieur ; `side` = −1 pour la droite du personnage (gauche de l'image)."""
    d = b - a
    return math.degrees(math.atan2(side * d[0], d[1]))


def _bend(a, b, c) -> float:
    u, v = b - a, c - b
    cos = float(u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-6))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def geometry(entry: dict) -> dict:
    """Les mesures de pose d'un relevé, et ce qui ne passe pas."""
    fails: list[str] = []
    if not entry.get("body"):
        return {"ok": False, "fails": ["personne n'est relevé dans l'image"]}
    p = _pts(entry)
    missing = [i for i in NEEDED if p[i] is None]
    if missing:
        return {"ok": False, "fails": [f"points non relevés : {missing}"]}
    w, h = entry["width"], entry["height"]
    arms = {"right": _down_angle(p[RSH], p[RWR]), "left": _down_angle(p[LSH], p[LWR])}
    elbows = {"right": _bend(p[RSH], p[REL], p[RWR]), "left": _bend(p[LSH], p[LEL], p[LWR])}
    legs = {"right": _outward(p[RHIP], p[RANK], -1), "left": _outward(p[LHIP], p[LANK], +1)}
    hips = float(np.linalg.norm(p[LHIP] - p[RHIP]))
    ankles = float(np.linalg.norm(p[LANK] - p[RANK]))
    mid_hip = (p[LHIP] + p[RHIP]) / 2
    torso = float(np.linalg.norm(mid_hip - p[NECK]))
    shoulders = float(np.linalg.norm(p[LSH] - p[RSH]))
    head = float(np.linalg.norm(p[NECK] - p[NOSE]))
    shin = float(np.linalg.norm(p[LANK] - p[LKNE]) + np.linalg.norm(p[RANK] - p[RKNE])) / 2
    top = p[NOSE][1] - 0.75 * head          # sommet du crâne : le nez en est à 0,6 cou-nez, cheveux en plus
    bottom = max(p[LANK][1], p[RANK][1]) + 0.18 * shin   # plante des pieds
    xs = [q[0] for q in p if q is not None]
    for hand in ("left_hand", "right_hand"):
        xs += [q[0] for q in entry.get(hand) or [] if q and q[2] >= MIN_SCORE]
    frame = {"top": top / h, "bottom": 1 - bottom / h, "left": min(xs) / w, "right": 1 - max(xs) / w}
    ratio = ankles / max(hips, 1e-6)
    facing = shoulders / max(torso, 1e-6)
    for side, a in arms.items():
        if abs(a - ARM_TARGET) > ARM_TOL:
            fails.append(f"bras {side} à {a:.0f}° de la verticale (45 ± {ARM_TOL:g})")
        if elbows[side] > ELBOW_BEND:
            fails.append(f"coude {side} plié de {elbows[side]:.0f}°")
    if abs(arms["left"] - arms["right"]) > ARM_SPREAD:
        fails.append(f"bras dissymétriques ({arms['left']:.0f}° / {arms['right']:.0f}°)")
    for side, a in legs.items():
        if not LEG_RANGE[0] <= a <= LEG_RANGE[1]:
            fails.append(f"jambe {side} à {a:.0f}° de la verticale")
    if not ANKLE_RATIO[0] <= ratio <= ANKLE_RATIO[1]:
        fails.append(f"écart des pieds {ratio:.2f} × celui des hanches")
    # De face : la droite du personnage à gauche de l'image (de dos,
    # DWPose inverse épaules et hanches), le nez entre les épaules (un 3/4
    # le décale d'un tiers de la carrure), les épaules larges.
    nose_offset = (p[NOSE][0] - (p[LSH][0] + p[RSH][0]) / 2) / max(shoulders, 1e-6)
    if p[LSH][0] <= p[RSH][0] or p[LHIP][0] <= p[RHIP][0]:
        fails.append("de dos")
    elif facing < SHOULDER_RATIO or abs(nose_offset) > NOSE_OFFSET or p[REYE] is None or p[LEYE] is None:
        fails.append(f"pas de face (carrure {facing:.2f}, nez décalé de {nose_offset:+.2f})")
    for edge, m in frame.items():
        if m < FRAME_MARGIN:
            fails.append(f"coupé au bord {edge}")
    if entry.get("people", 1) > 1:
        fails.append(f"{entry['people']} personnes dans l'image")
    arm_err = (abs(arms["left"] - ARM_TARGET) + abs(arms["right"] - ARM_TARGET)) / 2
    pose_score = max(0.0, 1 - arm_err / 20) * max(0.0, 1 - max(elbows.values()) / 60)
    return {"ok": not fails, "fails": fails, "pose_score": round(pose_score, 3),
            "arms_deg": {k: round(v, 1) for k, v in arms.items()},
            "elbows_deg": {k: round(v, 1) for k, v in elbows.items()},
            "legs_deg": {k: round(v, 1) for k, v in legs.items()}, "ankle_ratio": round(ratio, 2),
            "facing": round(facing, 2), "nose_offset": round(float(nose_offset), 3), "frame": {k: round(float(v), 3) for k, v in frame.items()},
            "people": entry.get("people", 1)}


REGIONS = {  # (segments, épaisseur en fraction de la longueur du torse)
    "torso": None,
    "thighs": (((RHIP, RKNE), (LHIP, LKNE)), 0.22),
    "shins": (((RKNE, RANK), (LKNE, LANK)), 0.14),
    "arms": (((RSH, REL), (LSH, LEL), (REL, RWR), (LEL, LWR)), 0.10),
    "head": None,
}
WEIGHTS = {"torso": 0.35, "thighs": 0.2, "shins": 0.1, "arms": 0.15, "head": 0.2}


def _masks(entry: dict, size: tuple[int, int]) -> dict[str, np.ndarray]:
    p = _pts(entry)
    if any(p[i] is None for i in (NECK, NOSE, RSH, LSH, RHIP, LHIP)):
        return {}
    torso = float(np.linalg.norm((p[LHIP] + p[RHIP]) / 2 - p[NECK]))
    out = {}
    for name, spec in REGIONS.items():
        m = Image.new("L", size, 0)
        d = ImageDraw.Draw(m)
        if name == "torso":
            quad = np.array([p[RSH], p[LSH], p[LHIP], p[RHIP]])
            c = quad.mean(0)
            d.polygon([tuple(c + (q - c) * 0.7) for q in quad], fill=255)
        elif name == "head":
            r = 0.45 * float(np.linalg.norm(p[NECK] - p[NOSE]))
            cx, cy = p[NOSE][0], p[NOSE][1] - 0.3 * r
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        else:
            segs, width = spec
            for a, b in segs:
                if p[a] is not None and p[b] is not None:
                    d.line([tuple(p[a]), tuple(p[b])], fill=255, width=max(2, int(width * torso)))
        out[name] = np.asarray(m) > 0
    return out


def _hist(lab: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    px = lab[mask]
    if len(px) < 50:
        return None
    l_h = np.histogram(px[:, 0], bins=8, range=(0, 256))[0].astype(float)
    ab_h = np.histogram2d(px[:, 1], px[:, 2], bins=12, range=((0, 256), (0, 256)))[0].ravel().astype(float)
    return np.concatenate([0.4 * l_h / l_h.sum(), 0.6 * ab_h / ab_h.sum()])


def outfit(ref_img: Path, ref: dict, img: Path, cand: dict) -> dict:
    """Similarité de couleur région par région, 0 à 1."""
    a, b = Image.open(ref_img).convert("RGB"), Image.open(img).convert("RGB")
    la, lb = np.asarray(a.convert("LAB")), np.asarray(b.convert("LAB"))
    ma, mb = _masks(ref, a.size), _masks(cand, b.size)
    sims = {}
    for name in REGIONS:
        if name not in ma or name not in mb:
            continue
        ha, hb = _hist(la, ma[name]), _hist(lb, mb[name])
        if ha is not None and hb is not None:
            sims[name] = round(float(np.minimum(ha, hb).sum()), 3)
    total = sum(WEIGHTS[k] * v for k, v in sims.items()) / max(sum(WEIGHTS[k] for k in sims), 1e-6)
    return {"similarity": round(total, 3), "regions": sims}


def score(ref_img: Path, ref: dict, img: Path, cand: dict) -> dict:
    """La mesure complète d'une proposition contre le plein pied validé."""
    geo = geometry(cand)
    if not geo.get("arms_deg"):
        return {**geo, "score": 0.0}
    fit = outfit(ref_img, ref, img, cand)
    if not fit["regions"]:
        raise ChainError("le plein pied validé n'a pas pu être relevé par DWPose : pas de tenue à comparer")
    fails = list(geo["fails"])
    ref_facing = geometry(ref).get("facing")
    if ref_facing and geo["facing"] < FACING_REL * ref_facing and not any("face" in f or "dos" in f for f in fails):
        fails.append(f"carrure à {geo['facing'] / ref_facing:.0%} de celle du plein pied : tourné")
    if fit["similarity"] < OUTFIT_MIN:
        fails.append(f"tenue à {fit['similarity']:.2f} du plein pied (seuil {OUTFIT_MIN})")
    low = [k for k, v in fit["regions"].items() if v < REGION_MIN]
    if low:
        fails.append(f"régions trop différentes : {', '.join(low)}")
    return {**geo, "ok": not fails, "fails": fails, "outfit": fit,
            "score": round(0.5 * geo["pose_score"] + 0.5 * fit["similarity"], 3)}
