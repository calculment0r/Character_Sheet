"""Le squelette SOMA d'un personnage, et son bind en A-pose (§8, §10).

La convention, tenue partout dans la chaîne :

  - repères d'articulation alignés sur le monde en T-pose : en T-pose,
    chaque articulation a une orientation monde identité. Une rotation
    locale est donc « relative à la T-pose », exactement comme les
    rotations SOMA / Kimodo une fois réexprimées dans ce repère ;
  - `offsets[j]` : le vecteur parent → articulation en T-pose, en mètres ;
  - `bind[j]`    : la rotation locale qui amène de la T-pose à l'A-pose
    du mesh. C'est le « delta de bind » du §8.2, rangé avec le rig.

Le mesh est lié en A-pose (les matrices de bind inverse sont celles de
l'A-pose), mais une rotation SOMA se joue telle quelle sur le nœud : la
T-pose n'est jamais un bind, c'est un repère.

Une rotation SOMA exprimée dans ses propres repères d'articulation
(orientation monde W_T[j] en T-pose) se ramène ici par conjugaison :
    L_ici[j] = W_T[j] · L_soma[j] · W_T[j]ᵀ
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config
from .gltf import GLB, matrix_from_quat, quat_from_axis_angle, quat_from_matrix, quat_mul

DATA = config.REPO / "data" / "soma77.json"


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """La plus petite rotation qui amène la direction a sur b, en quaternion."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    c = np.cross(a, b)
    d = float(np.dot(a, b))
    if d < -0.999999:
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        return quat_from_axis_angle(axis, np.pi)
    q = np.array([c[0], c[1], c[2], 1.0 + d])
    return q / np.linalg.norm(q)


@dataclass
class Skeleton:
    names: list[str]
    parents: list[int]
    offsets: np.ndarray                     # (J, 3)
    bind: np.ndarray                        # (J, 4)
    roles: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.names)

    def index(self, name: str) -> int:
        return self.names.index(name)

    def role(self, role: str) -> int | None:
        name = self.roles.get(role)
        return self.names.index(name) if name in self.names else None

    # ── cinématique directe ────────────────────────────────────────

    def fk(self, quats: np.ndarray, root: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(J, 4) → rotations monde (J, 3, 3) et positions (J, 3)."""
        local = matrix_from_quat(quats)
        rot = np.zeros((self.count, 3, 3))
        pos = np.zeros((self.count, 3))
        for j, p in enumerate(self.parents):
            if p < 0:
                rot[j] = local[j]
                pos[j] = self.offsets[j] if root is None else root
            else:
                rot[j] = rot[p] @ local[j]
                pos[j] = pos[p] + rot[p] @ self.offsets[j]
        return rot, pos

    def bind_matrices(self) -> np.ndarray:
        """Les transformations monde de l'A-pose, (J, 4, 4)."""
        rot, pos = self.fk(self.bind)
        m = np.tile(np.eye(4), (self.count, 1, 1))
        m[:, :3, :3] = rot
        m[:, :3, 3] = pos
        return m

    def hip_height(self) -> float:
        _, pos = self.fk(np.tile([0.0, 0.0, 0.0, 1.0], (self.count, 1)))
        return float(pos[0, 1] - pos[:, 1].min())

    # ── construction ───────────────────────────────────────────────

    @classmethod
    def from_apose(cls, names: list[str], parents: list[int], positions: np.ndarray,
                   directions: dict[str, tuple[str, list[float]]], roles: dict[str, str] | None = None) -> "Skeleton":
        """Retrouve la T-pose et le delta de bind à partir des positions
        des articulations dans l'A-pose du mesh.

        `directions[nom] = (enfant, direction)` : la direction, en T-pose
        SOMA, de l'os qui va de cette articulation à cet enfant — +X le
        long du bras gauche, -Y le long des jambes. Une articulation sans
        direction (la racine) n'a pas de rotation de bind : elle porte
        l'orientation du corps entier, pas la cambrure du dos."""
        n = len(names)
        bind = np.tile([0.0, 0.0, 0.0, 1.0], (n, 1))
        world = [np.eye(3)] * n
        offsets = np.zeros((n, 3))
        for j in range(n):          # les parents précèdent toujours leurs enfants
            p = parents[j]
            parent_world = world[p] if p >= 0 else np.eye(3)
            if p < 0:
                offsets[j] = positions[j]
            else:
                offsets[j] = parent_world.T @ (positions[j] - positions[p])
            local = np.eye(3)
            if names[j] in directions:
                child_name, direction = directions[names[j]]
                bone = positions[names.index(child_name)] - positions[j]
                if np.linalg.norm(bone) > 1e-6:
                    target = parent_world.T @ bone
                    q = rotation_between(np.asarray(direction, dtype=np.float64), target)
                    bind[j] = q
                    local = matrix_from_quat(q)
            world[j] = parent_world @ local
        return cls(list(names), list(parents), offsets, bind, dict(roles or {}))

    # ── sérialisation ──────────────────────────────────────────────

    def to_json(self) -> dict:
        return {"names": self.names, "parents": self.parents, "offsets": self.offsets.round(6).tolist(),
                "bind": self.bind.round(8).tolist(), "roles": self.roles,
                "convention": "repères alignés sur le monde en T-pose ; bind = T-pose → A-pose ; mètres, Y haut, +Z devant"}

    @classmethod
    def from_json(cls, data: dict) -> "Skeleton":
        return cls(data["names"], data["parents"], np.asarray(data["offsets"], dtype=np.float64),
                   np.asarray(data["bind"], dtype=np.float64), data.get("roles", {}))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Skeleton":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def soma_spec() -> dict:
    """Noms, parents, pose neutre, rôles et masques du squelette SOMA 77,
    relevés une fois dans le code de Kimodo et rangés dans data/soma77.json."""
    return json.loads(DATA.read_text(encoding="utf-8"))


def tpose_directions(spec: dict) -> dict[str, tuple[str, list[float]]]:
    """La direction de chaque os en T-pose, lue sur la pose neutre SOMA."""
    neutral = np.asarray(spec["neutral"])
    names = spec["names"]
    out = {}
    for joint, child in spec["primary_child"].items():
        d = neutral[names.index(child)] - neutral[names.index(joint)]
        if np.linalg.norm(d) > 1e-6:
            out[joint] = (child, (d / np.linalg.norm(d)).tolist())
    return out


def soma_apose(spec: dict) -> np.ndarray:
    """Les 77 articulations SOMA en A-pose : la pose neutre, bras baissés
    à 45° et jambes un peu ouvertes (§8.1). Sert au mannequin factice."""
    names, parents = spec["names"], spec["parents"]
    neutral = np.asarray(spec["neutral"], dtype=np.float64)
    quats = np.tile([0.0, 0.0, 0.0, 1.0], (len(names), 1))
    for joint, (axis, deg) in spec["apose"].items():
        quats[names.index(joint)] = quat_from_axis_angle(axis, np.radians(deg))
    offsets = np.array([neutral[j] - (neutral[p] if p >= 0 else 0) for j, p in enumerate(parents)])
    sk = Skeleton(names, parents, offsets, quats)
    return sk.fk(quats)[1]


def soma_rig_from_apose(positions: np.ndarray, spec: dict | None = None) -> "Skeleton":
    spec = spec or soma_spec()
    return Skeleton.from_apose(spec["names"], spec["parents"], positions, tpose_directions(spec), spec["roles"])


def layer_masks(names: list[str], spec: dict) -> dict[str, np.ndarray]:
    """Les masques d'articulations des quatre couches du §11.3."""
    masks = {}
    for layer, members in spec["masks"].items():
        masks[layer] = np.array([n in members for n in names])
    # Le corps prend tout ce qui n'est ni doigt ni visage.
    other = masks["hands_l"] | masks["hands_r"] | masks["face"]
    masks["body"] = ~other
    return masks


# ── les cinq poses de contrôle du §10.3 ────────────────────────────

def _q(axis, deg):
    return quat_from_axis_angle(axis, np.radians(deg))


X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

CONTROL_POSES = {
    # rôle → (axe, degrés), rotations locales relatives à la T-pose
    "arms_down": {"l_arm": (Z, -78), "r_arm": (Z, 78)},
    "arms_up": {"l_arm": (Z, 75), "r_arm": (Z, -75)},
    "squat": {"l_upleg": (X, -95), "r_upleg": (X, -95), "l_leg": (X, 110), "r_leg": (X, 110),
              "spine1": (X, -15), "l_arm": (Z, -60), "r_arm": (Z, 60), "_drop": 0.42},
    "twist": {"spine1": (Y, 15), "spine2": (Y, 12), "chest": (Y, 12), "l_arm": (Z, -70), "r_arm": (Z, 70)},
    "walk_step": {"l_upleg": (X, -28), "r_upleg": (X, 18), "l_leg": (X, 25), "r_leg": (X, 8),
                  "l_arm": (Z, -75), "r_arm": (Z, 75), "l_forearm": (Y, -15), "r_forearm": (Y, 15)},
}
CONTROL_LABELS = {"arms_down": "bras le long du corps", "arms_up": "bras levés", "squat": "accroupi",
                  "twist": "torsion", "walk_step": "pas de marche"}


def control_pose(sk: Skeleton, name: str) -> tuple[np.ndarray, np.ndarray]:
    """Rend (quaternions (J, 4), racine (3,)) pour une pose de contrôle.
    Les articulations non citées restent en T-pose, sauf les bras, que
    chaque pose place explicitement."""
    spec = CONTROL_POSES[name]
    quats = np.tile([0.0, 0.0, 0.0, 1.0], (sk.count, 1))
    for role, value in spec.items():
        if role.startswith("_"):
            continue
        j = sk.role(role)
        if j is not None:
            quats[j] = quat_mul(quats[j], _q(*value))
    root = sk.offsets[0].copy()
    root[1] -= spec.get("_drop", 0.0)
    return quats, root


# ── écriture du rig en glTF ────────────────────────────────────────

def add_skeleton(g: GLB, sk: Skeleton) -> tuple[list[int], int]:
    """Ajoute les nœuds du squelette, posés en A-pose (le bind). Rend les
    indices des nœuds et celui de la racine."""
    nodes = []
    for j, name in enumerate(sk.names):
        nodes.append(g.node(name, translation=sk.offsets[j], rotation=sk.bind[j]))
    for j, p in enumerate(sk.parents):
        if p >= 0:
            g.doc["nodes"][nodes[p]].setdefault("children", []).append(nodes[j])
    root = nodes[sk.parents.index(-1)]
    g.doc["scenes"][0]["nodes"].append(root)
    return nodes, root


def add_clip(g: GLB, sk: Skeleton, name: str, quats: np.ndarray, root: np.ndarray, fps: float) -> int:
    """Un clip : (T, J, 4) rotations locales relatives à la T-pose, et
    (T, 3) position de la racine. Les nœuds sont retrouvés par nom."""
    index = g.node_index()
    frames = quats.shape[0]
    times = np.arange(frames, dtype=np.float32) / fps
    channels = []
    for j, jname in enumerate(sk.names):
        channels.append((index[jname], "rotation", times, quats[:, j].astype(np.float32)))
    channels.append((index[sk.names[sk.parents.index(-1)]], "translation", times, root.astype(np.float32)))
    return g.animation(name, channels)


def mat4_to_quat(m: np.ndarray) -> np.ndarray:
    return quat_from_matrix(m[..., :3, :3])
