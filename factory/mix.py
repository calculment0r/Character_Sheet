"""Le mixage de la timeline (§11.3, §13.3).

Pistes typées — body, hands_l, hands_r, face — chacune faite de
morceaux de prises. Trois règles du brief :

  - mixage par masque d'articulations, jamais par fondu global : le
    corps vient d'une piste, les doigts d'une autre, le visage d'une
    troisième, et aucune n'écrase les autres ;
  - fondu sur les rotations en quaternions (slerp), jamais sur les
    matrices ;
  - un lissage séparé par couche : les mains et le visage n'ont pas la
    constante de temps du corps.

Les rotations sont locales, relatives à la T-pose SOMA (la pose zéro de
Kimodo), en convention « repères alignés sur le monde » — voir
`skeleton.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .gltf import quat_from_matrix, slerp

LAYERS = ("body", "hands_l", "hands_r", "face")

# Constante de lissage par couche, en frames : le corps est lent, les
# doigts vifs, le visage entre les deux.
SMOOTHING = {"body": 0.0, "hands_l": 1.5, "hands_r": 1.5, "face": 1.0}


@dataclass
class Take:
    quats: np.ndarray          # (T, J, 4) [x, y, z, w]
    root: np.ndarray           # (T, 3) mètres
    fps: float
    names: list[str]
    expression: np.ndarray | None = None   # (T, 72) MHR, facultatif

    @property
    def frames(self) -> int:
        return self.quats.shape[0]


def load_take(path: str | Path) -> Take:
    """Une prise au format de la chaîne : rotations locales (quaternions
    ou matrices), racine, cadence, noms d'articulations."""
    data = np.load(path, allow_pickle=False)
    if "local_quats" in data:
        quats = data["local_quats"].astype(np.float64)
    elif "local_rot_mats" in data:
        quats = quat_from_matrix(data["local_rot_mats"])
    else:
        raise ValueError(f"{path} : ni local_quats ni local_rot_mats")
    root = data["root_positions"].astype(np.float64) if "root_positions" in data else np.zeros((len(quats), 3))
    names = [str(n) for n in data["joint_names"]] if "joint_names" in data else []
    expr = data["expression"].astype(np.float64) if "expression" in data else None
    return Take(quats, root, float(data["fps"]) if "fps" in data else 30.0, names, expr)


def _sample(take: Take, f: float) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """La prise à une frame fractionnaire : slerp entre les voisines."""
    f = min(max(f, 0.0), take.frames - 1)
    i0 = int(np.floor(f))
    i1 = min(i0 + 1, take.frames - 1)
    t = f - i0
    q = slerp(take.quats[i0], take.quats[i1], t) if t > 1e-9 else take.quats[i0]
    root = take.root[i0] * (1 - t) + take.root[i1] * t
    expr = None
    if take.expression is not None:
        expr = take.expression[i0] * (1 - t) + take.expression[i1] * t
    return q, root, expr


def _duration(track: dict, take: Take, fps: float) -> float:
    """Durée d'un morceau sur la timeline, en frames de la timeline."""
    span = max(0, (track.get("out") or take.frames - 1) - track.get("in", 0))
    return span * (fps / take.fps) / max(track.get("time_warp", 1.0), 1e-6)


def _segment_frame(track: dict, take: Take, fps: float, t: float) -> float:
    return track.get("in", 0) + (t - track.get("at", 0)) * track.get("time_warp", 1.0) * (take.fps / fps)


def length(tracks: list[dict], takes: dict[str, Take], fps: float) -> int:
    end = 0.0
    for tr in tracks:
        end = max(end, tr.get("at", 0) + _duration(tr, takes[tr["take_id"]], fps))
    return int(np.ceil(end)) + 1


def _smooth(q: np.ndarray, tau: float) -> np.ndarray:
    """Lissage exponentiel aller-retour, sur la sphère : pas de retard."""
    if tau <= 0 or len(q) < 3:
        return q
    a = 1.0 / (1.0 + tau)
    out = q.copy()
    for i in range(1, len(out)):
        out[i] = slerp(out[i - 1], out[i], a)
    for i in range(len(out) - 2, -1, -1):
        out[i] = slerp(out[i + 1], out[i], a)
    return out


def _root_offsets(segs: list[dict], takes: dict[str, Take], fps: float) -> list[np.ndarray]:
    """Chaque morceau de corps repart là où le précédent en est : Kimodo
    ramène la racine en XZ = (0, 0) à la frame 0 de chaque prise, et
    sans ce décalage le personnage reviendrait au point de départ à
    chaque enchaînement. La hauteur, elle, reste absolue."""
    offsets = [np.zeros(3)]
    for k in range(1, len(segs)):
        prev, cur = segs[k - 1], segs[k]
        prev_take, cur_take = takes[prev["take_id"]], takes[cur["take_id"]]
        t = min(cur.get("at", 0), prev.get("at", 0) + _duration(prev, prev_take, fps))
        _, prev_root, _ = _sample(prev_take, _segment_frame(prev, prev_take, fps, t))
        _, cur_root, _ = _sample(cur_take, _segment_frame(cur, cur_take, fps, cur.get("at", 0)))
        delta = prev_root + offsets[k - 1] - cur_root
        delta[1] = 0.0
        offsets.append(delta)
    return offsets


def evaluate(tracks: list[dict], takes: dict[str, Take], masks: dict[str, np.ndarray], *,
             joints: int, fps: float, rest: np.ndarray | None = None) -> dict:
    """Rend la timeline mixée : quaternions (T, J, 4), racine (T, 3),
    expression (T, 72) ou None."""
    total = length(tracks, takes, fps) if tracks else 1
    ident = np.tile([0.0, 0.0, 0.0, 1.0], (joints, 1))
    base = np.repeat((rest if rest is not None else ident)[None], total, axis=0)
    root = np.zeros((total, 3))
    expr = None

    for layer in LAYERS:
        segs = sorted((t for t in tracks if t["type"] == layer), key=lambda t: t.get("at", 0))
        if not segs:
            continue
        mask = masks[layer]
        offsets = _root_offsets(segs, takes, fps) if layer == "body" else [np.zeros(3)] * len(segs)
        layer_q = np.zeros((total, joints, 4))
        layer_w = np.zeros(total)
        layer_root = np.zeros((total, 3))
        layer_expr = None
        for frame in range(total):
            active = []
            for k, tr in enumerate(segs):
                take = takes[tr["take_id"]]
                start = tr.get("at", 0)
                if start <= frame <= start + _duration(tr, take, fps):
                    fade = tr.get("fade", 0)
                    w = min(1.0, (frame - start) / fade) if fade > 0 else 1.0
                    active.append((tr, take, w, offsets[k]))
            if not active:
                continue
            # Le morceau le plus récent entre en fondu par-dessus le précédent.
            tr, take, _, off = active[0]
            q, r, e = _sample(take, _segment_frame(tr, take, fps, frame))
            r = r + off
            for tr2, take2, w2, off2 in active[1:]:
                q2, r2, e2 = _sample(take2, _segment_frame(tr2, take2, fps, frame))
                r2 = r2 + off2
                q = slerp(q, q2, w2)
                r = r * (1 - w2) + r2 * w2
                if e is not None and e2 is not None:
                    e = e * (1 - w2) + e2 * w2
            layer_q[frame] = q
            layer_w[frame] = active[-1][0].get("weight", 1.0)
            layer_root[frame] = r
            if e is not None:
                if layer_expr is None:
                    layer_expr = np.zeros((total, e.shape[0]))
                layer_expr[frame] = e

        layer_q[:, mask] = _smooth(layer_q[:, mask], SMOOTHING[layer])
        on = layer_w > 0
        # La couche ne touche que ses articulations, avec son poids.
        for frame in np.nonzero(on)[0]:
            base[frame, mask] = slerp(base[frame, mask], layer_q[frame, mask], layer_w[frame])
        if layer == "body":
            root[on] = layer_root[on]
        if layer == "face" and layer_expr is not None:
            expr = layer_expr

    return {"quats": base, "root": root, "expression": expr, "frames": total, "fps": fps}
