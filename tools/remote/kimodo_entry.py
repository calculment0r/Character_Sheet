"""Kimodo sur DGX2 : un job.json -> un motion.npz au format de la chaîne.

    ~/kimodo/.venv/bin/python kimodo_entry.py --job job.json --out motion.npz

job.json : {"prompt": str, "frames": int, "fps": float, "constraints": [...],
            "heading": float (radians, 0 = face à +Z, lacet positif vers +X), "seed": int}

`constraints` est la liste du format CLI de Kimodo (le contenu d'un
constraints.json) : types root2d, fullbody, left-hand, right-hand,
left-foot, right-foot, end-effector (+ joint_names). Leurs frame_indices
sont en images du modèle (30 i/s), leurs positions dans le repère canonique
de Kimodo (racine près de XZ = (0, 0) à l'image 0).

Tout passe par les fonctions de Kimodo, comme kimodo/scripts/generate.py :
load_model, load_constraints_lst, model(..., multi_prompt=True) qui rend
déjà du SOMA 77 (output_to_SOMASkeleton77, mains relâchées),
matrix_to_quaternion, et resample_motion_dict_to_kimodo_fps si fps != 30.
Aucune conversion d'axes : identité locale = pose neutre SOMA (joints.p),
repères alignés sur le monde, mètres, Y en haut, +Z devant.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# ── Réglages : le modèle et ses poids, à un seul endroit ─────────────
KIMODO_CHECKPOINTS = Path.home() / "kimodo" / "checkpoints"  # contient Kimodo-SOMA-RP-v1/
MODEL_ID = "Kimodo-SOMA-RP-v1"  # pas l'alias "kimodo-soma-rp", qui pointe sur la v1.1
DIFFUSION_STEPS = 100  # défauts du CLI (generate.py)
NUM_TRANSITION_FRAMES = 5
POSTPROCESS = True  # nettoyage du glissement des pieds (motion_correction)

os.environ.setdefault("CHECKPOINT_DIR", str(KIMODO_CHECKPOINTS))
os.environ.setdefault("TEXT_ENCODER_MODE", "local")  # pas de sonde du serveur 127.0.0.1:9550
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # encodeur de texte déjà en cache, pas de téléchargement surprise

# Un dossier qui contient le dépôt ~/kimodo (sans __init__.py) masquerait le
# paquet installé : "import kimodo" y trouverait un namespace vide.
sys.path[:] = [p for p in sys.path
               if not ((Path(p or ".") / "kimodo").is_dir() and not (Path(p or ".") / "kimodo" / "__init__.py").exists())]

import numpy as np  # noqa: E402
import torch  # noqa: E402

from kimodo import load_model  # noqa: E402
from kimodo.constraints import load_constraints_lst  # noqa: E402
from kimodo.exports.motion_io import resample_motion_dict_to_kimodo_fps  # noqa: E402
from kimodo.geometry import matrix_to_quaternion  # noqa: E402
from kimodo.skeleton import SOMASkeleton77  # noqa: E402
from kimodo.tools import seed_everything  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model, resolved = load_model(MODEL_ID, device=device, default_family="Kimodo", return_resolved_name=True)
    if resolved != "kimodo-soma-rp-v1":
        sys.exit(f"modèle inattendu : {resolved}")
    model_fps = float(model.fps)

    # Kimodo génère à sa cadence ; on garde la durée demandée et on rééchantillonne après.
    fps = float(job.get("fps") or model_fps)
    same_rate = abs(fps - model_fps) < 1e-6
    n_gen = int(job["frames"]) if same_rate else max(2, round(int(job["frames"]) * model_fps / fps))

    constraints = job.get("constraints") or []
    constraint_lst = load_constraints_lst(constraints, model.skeleton) if constraints else []  # comme generate.py:304
    if job.get("seed") is not None:
        seed_everything(int(job["seed"]))

    # Même appel que le CLI (generate.py:327), un seul segment, cap initial en plus.
    out = model(
        [job["prompt"]], [n_gen],
        constraint_lst=constraint_lst,
        num_denoising_steps=DIFFUSION_STEPS,
        num_samples=1,
        multi_prompt=True,
        num_transition_frames=NUM_TRANSITION_FRAMES,
        post_processing=POSTPROCESS,
        return_numpy=True,
        first_heading_angle=torch.tensor([float(job.get("heading") or 0.0)]),
    )
    local = out["local_rot_mats"][0]  # (T, 77, 3, 3), déjà SOMA 77
    root = out["root_positions"][0]  # (T, 3), Hips en mètres
    contacts = out["foot_contacts"][0][:, [0, 1, 3, 4]]  # (T, 6) -> (T, 4) : LeftFoot, LeftToeBase, RightFoot, RightToeBase

    if not same_rate:
        m, _ = resample_motion_dict_to_kimodo_fps(
            {"local_rot_mats": torch.from_numpy(local), "root_positions": torch.from_numpy(root)},
            SOMASkeleton77(), model_fps, fps)
        local, root = m["local_rot_mats"].numpy(), m["root_positions"].numpy()
        contacts = m["foot_contacts"].numpy()  # recalculés (hauteur + vitesse), mêmes 4 canaux

    q = matrix_to_quaternion(torch.from_numpy(local).double()).numpy()  # [w, x, y, z]
    q /= np.linalg.norm(q, axis=-1, keepdims=True)
    q[0] *= np.where(q[0, :, :1] < 0, -1.0, 1.0)  # w >= 0 à l'image 0
    flips = np.sign(np.sum(q[1:] * q[:-1], axis=-1))  # puis continuité : q et -q sont la même rotation
    flips[flips == 0] = 1.0
    q[1:] *= np.cumprod(flips, axis=0)[..., None]

    names = list(model.output_skeleton.bone_order_names)  # = data/soma77.json à 58e7818
    tmp = Path(args.out + ".part")
    with open(tmp, "wb") as f:
        np.savez(
            f,
            local_quats=q[..., [1, 2, 3, 0]].astype(np.float32),  # [x, y, z, w]
            root_positions=root.astype(np.float32),
            fps=np.float32(fps),
            joint_names=np.array(names),
            source=np.array(job["prompt"]),
            engine=np.array(f"kimodo:{MODEL_ID}"),
            foot_contacts=contacts.astype(np.float32),
        )
    os.replace(tmp, args.out)
    print(json.dumps({"out": args.out, "frames": int(q.shape[0]), "fps": fps, "model": MODEL_ID}))


if __name__ == "__main__":
    main()
