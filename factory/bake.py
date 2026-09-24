"""La cuisson d'une timeline (§11.3) : mixage → NPZ somaskel77 plus le
canal d'expression MHR, et un GLB du personnage riggé qui joue le clip.

La racine est mise à l'échelle du personnage : Kimodo et SAM 3D Body
parlent pour un corps SOMA dont les hanches sont à 0,96 m ; un
personnage plus grand fait des pas plus longs, pas des pas qui glissent.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import gltf, mix, skeleton
from .motion import SOMA_HIP_HEIGHT
from .skeleton import Skeleton


def bake(*, tracks: list[dict], takes: dict[str, Path], rig_dir: Path, fps: float, out_dir: Path,
         name: str) -> dict:
    sk = Skeleton.load(rig_dir / "skeleton.json")
    spec = skeleton.soma_spec()
    loaded = {tid: mix.load_take(path) for tid, path in takes.items()}
    for tid, take in loaded.items():
        if take.names and take.names != sk.names:
            raise ValueError(f"prise {tid} : squelette différent de celui du rig ({len(take.names)} articulations)")
    masks = skeleton.layer_masks(sk.names, spec)
    res = mix.evaluate(tracks, loaded, masks, joints=sk.count, fps=fps)

    scale = sk.hip_height() / SOMA_HIP_HEIGHT
    root = res["root"] * scale
    # Sans couche de corps, la racine reste à sa place de repos.
    no_body = ~np.any(res["root"], axis=1)
    root[no_body] = sk.offsets[0]

    out_dir.mkdir(parents=True, exist_ok=True)
    arrays = {"local_rot_mats": gltf.matrix_from_quat(res["quats"]).astype(np.float32),
              "local_quats": res["quats"].astype(np.float32), "root_positions": root.astype(np.float32),
              "fps": np.float32(fps), "joint_names": np.array(sk.names), "skeleton": np.array("somaskel77"),
              "root_scale": np.float32(scale)}
    if res["expression"] is not None:
        arrays["expression"] = res["expression"].astype(np.float32)
    np.savez_compressed(out_dir / "anim.npz", **arrays)

    g = gltf.GLB.load(rig_dir / "rigged.glb")
    g.doc.pop("animations", None)        # les poses de contrôle n'ont rien à faire ici
    skeleton.add_clip(g, sk, f"timeline/{name}", res["quats"], root, fps)
    g.save(out_dir / "anim.glb")

    meta = {"frames": int(res["frames"]), "fps": fps, "seconds": round(res["frames"] / fps, 3),
            "root_scale": round(float(scale), 4), "expression": res["expression"] is not None,
            "tracks": tracks}
    (out_dir / "bake.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"npz": out_dir / "anim.npz", "glb": out_dir / "anim.glb", "meta": meta}
