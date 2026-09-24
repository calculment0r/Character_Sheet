"""Les prises de mouvement (§11) : Kimodo pour le mouvement inventé,
SAM 3D Body pour le mouvement lu dans une vidéo.

Une prise est un NPZ au format de la chaîne :
    local_quats    (T, 77, 4)  rotations locales relatives à la T-pose,
                               repères alignés sur le monde (skeleton.py)
    root_positions (T, 3)      la racine (Hips), en mètres, Y en haut
    fps, joint_names, source, engine
    expression     (T, 72)     facultatif — les paramètres MHR du visage
    foot_contacts  (T, 4)      facultatif — rendu par Kimodo

Kimodo canonicalise tout : racine en XZ = (0, 0) à la frame 0, cap
initial donné à part (`first_heading_angle`, 0 = face à +Z). Le format
de la chaîne garde cette convention.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config, skeleton
from .gltf import quat_from_axis_angle, quat_mul

# La hauteur de hanches du corps SOMA neutre, au-dessus de son point le
# plus bas. Le brief parle d'environ 0,96 m debout ; la pose neutre de
# Kimodo donne 1,005 m jusqu'au bout des orteils.
SOMA_HIP_HEIGHT = skeleton.soma_spec()["hip_height_neutral"]


def save_take(dest: Path, *, quats: np.ndarray, root: np.ndarray, fps: float, names: list[str],
              source: str, engine: str, extra: dict | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    arrays = {"local_quats": quats.astype(np.float32), "root_positions": root.astype(np.float32),
              "fps": np.float32(fps), "joint_names": np.array(names), "source": np.array(source),
              "engine": np.array(engine)}
    for k, v in (extra or {}).items():
        arrays[k] = v
    np.savez_compressed(dest, **arrays)
    return dest


# ── Kimodo ─────────────────────────────────────────────────────────

def kimodo(*, prompt: str, frames: int, fps: float, constraints: list[dict], heading: float, seed: int,
           dest: Path, report=lambda p, m: None) -> dict:
    backend = config.backend("kimodo")
    spec = skeleton.soma_spec()
    if backend == "stub":
        report(0.4, "factice · cycle de marche procédural")
        quats, root = _stub_walk(spec, frames, fps, seed, heading)
        save_take(dest, quats=quats, root=root, fps=fps, names=spec["names"], source="authored",
                  engine="kimodo:stub", extra={"prompt": np.array(prompt)})
    else:
        from . import motion_kimodo

        motion_kimodo.generate(prompt=prompt, frames=frames, fps=fps, constraints=constraints, heading=heading,
                               seed=seed, dest=dest, report=report)
    return {"npz": dest, "frames": frames, "fps": fps, "backend": backend}


# ── SAM 3D Body ────────────────────────────────────────────────────

def sam3dbody(*, video: Path, fps: float | None, dest: Path, report=lambda p, m: None) -> dict:
    backend = config.backend("sam3dbody")
    spec = skeleton.soma_spec()
    if backend == "stub":
        report(0.4, "factice · geste procédural à la place de la vidéo")
        frames = 90
        quats, root = _stub_wave(spec, frames, fps or 30.0)
        save_take(dest, quats=quats, root=root, fps=fps or 30.0, names=spec["names"], source="video",
                  engine="sam3dbody:stub", extra={"video": np.array(str(video))})
        return {"npz": dest, "frames": frames, "fps": fps or 30.0, "backend": backend}
    from . import motion_sam3d

    return motion_sam3d.run(video=video, fps=fps, dest=dest, report=report)


# ── gestes factices ────────────────────────────────────────────────

def _roles(spec: dict) -> dict[str, int]:
    return {role: spec["names"].index(name) for role, name in spec["roles"].items() if name in spec["names"]}


def _stub_walk(spec: dict, frames: int, fps: float, seed: int, heading: float) -> tuple[np.ndarray, np.ndarray]:
    """Une marche : jambes en balancier, bras le long du corps en
    opposition, racine qui avance à 1,2 m/s dans la direction du cap."""
    rng = np.random.default_rng(seed)
    r = _roles(spec)
    n = len(spec["names"])
    t = np.arange(frames) / fps
    phase = 2 * np.pi * t / 1.1 + rng.uniform(0, 0.3)
    quats = np.tile([0.0, 0.0, 0.0, 1.0], (frames, n, 1))

    def put(role, axis, deg):
        if role in r:
            for f in range(frames):
                quats[f, r[role]] = quat_mul(quats[f, r[role]], quat_from_axis_angle(axis, np.radians(deg[f])))

    X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
    put("l_upleg", X, -25 * np.sin(phase))
    put("r_upleg", X, 25 * np.sin(phase))
    put("l_leg", X, 30 * np.clip(np.sin(phase + 1.2), 0, None) + 5)
    put("r_leg", X, 30 * np.clip(np.sin(phase + 1.2 + np.pi), 0, None) + 5)
    # Le balancier d'abord, le bras baissé ensuite : q_balancier · q_baissé
    # baisse le bras puis le balance autour de l'axe des épaules.
    put("l_arm", X, 18 * np.sin(phase))
    put("r_arm", X, -18 * np.sin(phase))
    put("l_arm", Z, np.full(frames, -76.0))
    put("r_arm", Z, np.full(frames, 76.0))
    put("spine1", Y, 4 * np.sin(phase))
    heading_q = quat_from_axis_angle(Y, heading)
    if "hips" in r:
        for f in range(frames):
            quats[f, r["hips"]] = quat_mul(heading_q, quats[f, r["hips"]])
    speed = 1.2
    direction = np.array([np.sin(heading), 0.0, np.cos(heading)])
    root = np.zeros((frames, 3))
    root[:, 1] = SOMA_HIP_HEIGHT + 0.02 * np.cos(2 * phase)
    root += direction * speed * t[:, None]
    root[:, [0, 2]] -= root[0, [0, 2]]      # XZ à zéro à la frame 0, comme Kimodo
    return quats, root


def _stub_wave(spec: dict, frames: int, fps: float) -> tuple[np.ndarray, np.ndarray]:
    """Un salut de la main droite, sur place."""
    r = _roles(spec)
    n = len(spec["names"])
    t = np.arange(frames) / fps
    quats = np.tile([0.0, 0.0, 0.0, 1.0], (frames, n, 1))
    X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
    for f in range(frames):
        if "l_arm" in r:
            quats[f, r["l_arm"]] = quat_from_axis_angle(Z, np.radians(-76))
        if "r_arm" in r:
            quats[f, r["r_arm"]] = quat_from_axis_angle(Z, np.radians(-40))
        if "r_forearm" in r:
            quats[f, r["r_forearm"]] = quat_from_axis_angle(Z, np.radians(-70 + 25 * np.sin(2 * np.pi * t[f] * 1.5)))
    root = np.tile([0.0, SOMA_HIP_HEIGHT, 0.0], (frames, 1))
    return quats, root


# ── bibliothèque de mains (§13.1) ──────────────────────────────────

def hand_poses() -> dict:
    return json.loads((config.REPO / "data" / "hand_poses.json").read_text(encoding="utf-8"))["poses"]


def hand_pose(*, pose: str, frames: int, fps: float, dest: Path) -> dict:
    """Une prise figée des deux mains : c'est « ce qui sauvera 80 % des
    plans » quand aucune source ne donne les doigts. On la pose sur une
    piste hands_l ou hands_r ; le corps n'en prend rien."""
    lib = hand_poses()
    if pose not in lib:
        raise ValueError(f"pose de main inconnue : {pose} (possibles : {', '.join(lib)})")
    spec = skeleton.soma_spec()
    names = spec["names"]
    quats = np.tile([0.0, 0.0, 0.0, 1.0], (frames, len(names), 1))
    for joint, q in lib[pose].items():
        quats[:, names.index(joint)] = q
    root = np.tile([0.0, SOMA_HIP_HEIGHT, 0.0], (frames, 1))
    save_take(dest, quats=quats, root=root, fps=fps, names=names, source="authored",
              engine=f"mains:{pose}")
    return {"npz": dest, "frames": frames, "fps": fps, "backend": "builtin"}


def describe(npz: Path) -> dict:
    data = np.load(npz, allow_pickle=False)
    q = data["local_quats"] if "local_quats" in data else data["local_rot_mats"]
    return {"frames": int(q.shape[0]), "joints": int(q.shape[1]), "fps": float(data["fps"]),
            "engine": str(data["engine"]) if "engine" in data else "?"}


def load_constraints(path: str | None) -> list[dict]:
    """Le JSON de contraintes Kimodo (§12.2), relu tel quel : c'est le
    chargeur du CLI Kimodo qui l'interprète, pas nous."""
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("contraintes Kimodo : un tableau JSON d'objets {type, frame_indices, …} est attendu")
    return data
