"""Kimodo, sur un DGX, par ssh (`remote.py`).

Le travail se fait dans `tools/remote/kimodo_entry.py`, lancé dans le
venv de Kimodo : charger le modèle Kimodo-SOMA-RP-v1, générer, passer
de somaskel30 à SOMA 77 avec les fonctions de Kimodo, écrire le NPZ au
format de la chaîne. Kimodo range ses rotations comme la chaîne —
relatives à la pose neutre SOMA, repères alignés sur le monde — : aucune
conversion d'axes ici, seulement des vérifications.

Un vrai moteur qui échoue échoue : pas de repli sur le factice.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config, remote, skeleton
from .motion import save_take
from .project import ChainError

ENTRY = config.REPO / "tools" / "remote" / "kimodo_entry.py"


def generate(*, prompt: str, frames: int, fps: float, constraints: list[dict], heading: float, seed: int,
             dest: Path, report=lambda p, m: None) -> Path:
    work = dest.parent / ".kimodo"
    work.mkdir(parents=True, exist_ok=True)
    job = work / "job.json"
    job.write_text(json.dumps({"prompt": prompt, "frames": frames, "fps": fps, "constraints": constraints,
                               "heading": heading, "seed": seed}, ensure_ascii=False, indent=1), encoding="utf-8")
    got = remote.run("kimodo", ENTRY, args=["--job", "{job}/job.json", "--out", "{job}/motion.npz"],
                     inputs={"job.json": job}, outputs=["motion.npz"], workdir=work, report=report)
    data = np.load(got["motion.npz"], allow_pickle=False)
    spec = skeleton.soma_spec()
    quats, root = data["local_quats"], data["root_positions"]
    names = [str(n) for n in data["joint_names"]]
    if names != spec["names"]:
        raise ChainError("Kimodo : ordre des articulations différent de data/soma77.json")
    if quats.ndim != 3 or quats.shape[1:] != (77, 4) or root.shape != (quats.shape[0], 3):
        raise ChainError(f"Kimodo : formes inattendues {quats.shape} / {root.shape}")
    norms = np.linalg.norm(quats, axis=-1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        raise ChainError("Kimodo : quaternions non normés — l'ordre [x, y, z, w] est-il respecté ?")
    extra = {"prompt": np.array(prompt), "seed": np.int64(seed)}
    if "foot_contacts" in data:
        extra["foot_contacts"] = data["foot_contacts"]
    save_take(dest, quats=quats, root=root, fps=float(data["fps"]), names=names, source="authored",
              engine=str(data["engine"]), extra=extra)
    return dest
