#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
unirig_entry.py -- prediction UniRig (squelette + poids de skinning) SANS Blender.

Brouillon : pas encore execute. A copier sur DGX2 puis lancer depuis ~/UniRig :

    cd ~/UniRig && ~/UniRig/.venv/bin/python unirig_entry.py --mesh mesh.npz --out rig.npz

(ou depuis ailleurs avec --unirig-root ~/UniRig ou la variable UNIRIG_ROOT).

Entree mesh.npz
    vertices  float32 (V,3)  metres, Y haut, +Z avant (A-pose, pieds a y=0)
    faces     int32   (F,3)
    normals   optionnel -- IGNORE : UniRig recalcule ses normales avec trimesh
              (comme src/data/extract.py save_raw_data).

Sortie rig.npz (lisible sans allow_pickle)
    joint_names      (J,)   str      'bone_0'..'bone_{J-1}' avec la classe articulationxl
    parents          (J,)   int32    -1 pour la racine ; toujours parents[i] < i (racine = 0)
    joint_positions  (J,3)  float32  tetes d'os, MEME repere et unites que les vertices d'entree
    skin_joints      (V,k)  int32    indices dans joint_names (k = --topk, 4 par defaut)
    skin_weights     (V,k)  float32  normalises (somme 1 par sommet), indexes sur les vertices d'entree
    joint_tails      (J,3)  float32  queues d'os UniRig (meme repere) -- extra
    meta_json        ()     str      metadonnees (seed, classe, matrices de normalisation...) -- extra

Conventions UniRig (lues dans le code, commit 6793c66) et ce que fait ce script
  1. Repere : les donnees d'entrainement passent par l'import Blender (src/data/extract.py), donc
     repere Blender Z haut, personnage regardant -Y (cf. commentaire camera '+y' de
     src/data/vertex_group.py). L'import glTF de Blender fait (x, y, z)_gltf -> (x, -z, y)_blender.
     On applique la meme rotation R_IN_TO_BL aux vertices d'entree, et R^T aux joints en sortie.
  2. Pre-traitement (extract.py save_raw_data) : trimesh.Trimesh(process=True) -> fusion des
     sommets de meme position, decimation fast_simplification si F > 50000, normales trimesh.
  3. Normalisation (AugmentAffine, normalize_into [-1, 1]) : centre de boite englobante, division
     par la demi-plus-grande-dimension. Etape squelette : boite des vertices (matrice T1).
     Etape skin : l'entree est la sortie de l'etape squelette (deja normalisee) et la boite
     inclut les joints (matrice T2). Les deux matrices sont gardees et inversees exactement.
  4. Squelette : GPT (OPT-350m) autoregressif, coordonnees quantifiees sur 256 niveaux dans
     [-1, 1] (pas ~0.8 % de la plus grande dimension). Classe 'articulationxl' (config
     ar_inference_articulationxl.yaml) -> noms generiques bone_i (src/data/order.py make_names).
     Ordre : sequence generee (DFS), parents[i] < i.
  5. Skin : predit sur 32768 points echantillonnes (8192 sommets + 24576 points de surface du
     maillage pre-traite/decime), puis UniRig le reporte sur les sommets avec
     src/system/skin.py reskin() (mediane des 7 echantillons les plus proches + 1 pas de
     diffusion le long des aretes, seuil 0.03). ICI : reskin() est appele directement sur les
     sommets d'entree fusionnes par position (pleine resolution, pas la version decimee), puis
     le resultat est recopie sur les sommets dupliques (coutures UV) via l'index de fusion.
     Les poids sont donc indexes exactement sur les vertices d'entree ; puis top-k + normalisation
     (UniRig garde aussi 4 influences : group_per_vertex=4 dans merge.py / skin.py).
  6. Pas de Blender : l'etape extract (bpy) est remplacee par 1-2, les writers FBX (bpy) et
     src/inference/merge.py (bpy) ne sont pas utilises.
  7. flash_attn : si le module compile est importable, on garde la config d'origine
     (OPT en flash_attention_2, PTv3 enable_flash=True). Sinon : OPT en 'sdpa', PTv3
     enable_flash=False (chemin attention PyTorch prevu par le code), et un shim pur PyTorch
     de flash_attn.modules.mha.MHA (UniRig l'utilise avec use_flash_attn=False, donc chemin
     CrossAttention einsum/softmax identique au vrai module).
"""

import argparse
import contextlib
import importlib.machinery
import json
import math
import os
import sys
import time
import types

import numpy as np

# Repere pipeline (Y haut, +Z avant)  ->  repere Blender/UniRig (Z haut, -Y avant)
# v_bl = R @ v_in   ;   v_in = R^T @ v_bl   (en lignes : v_bl = v_in @ R.T ; v_in = v_bl @ R)
R_IN_TO_BL = np.array([[1.0, 0.0, 0.0],
                       [0.0, 0.0, -1.0],
                       [0.0, 1.0, 0.0]], dtype=np.float64)

SKELETON_TASK = "configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml"
SKIN_TASK = "configs/task/quick_inference_unirig_skin.yaml"

# (chemin local cree a l'installation -> symlink vers le cache HF, nom de fichier HF)
CKPTS = {
    "skeleton": ("experiments/skeleton/articulation-xl_quantization_256/model.ckpt",
                 "skeleton/articulation-xl_quantization_256/model.ckpt"),
    "skin": ("experiments/skin/articulation-xl/model.ckpt",
             "skin/articulation-xl/model.ckpt"),
}

# parametres de reskin() identiques a SkinWriter.write_on_batch_end (src/system/skin.py)
RESKIN_KWARGS = dict(sample_method="median", alpha=2.0, threshold=0.03)


def log(msg):
    print(f"[unirig_entry {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------------------------
# arguments / chemins
# --------------------------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="UniRig squelette + skinning sans Blender")
    p.add_argument("--mesh", required=True, help="mesh.npz (vertices, faces[, normals])")
    p.add_argument("--out", required=True, help="rig.npz de sortie")
    p.add_argument("--unirig-root", default=None, help="racine du repo UniRig (defaut : auto)")
    p.add_argument("--seed", type=int, default=12345,
                   help="graine (defaut identique a launch/inference/generate_*.sh)")
    p.add_argument("--skeleton-retries", type=int, default=3,
                   help="essais supplementaires (graine+1, +2...) si la generation echoue")
    p.add_argument("--cls", default=None,
                   help="classe de squelette imposee (defaut config : articulationxl ; "
                        "'vroid' donnerait des noms J_Bip_* mais le checkpoint publie est "
                        "entraine sur Articulation-XL2.0)")
    p.add_argument("--faces-target", type=int, default=50000,
                   help="decimation si plus de faces (defaut extract.sh : 50000)")
    p.add_argument("--topk", type=int, default=4, help="influences gardees par sommet")
    p.add_argument("--attn", choices=["auto", "flash", "sdpa"], default="auto")
    p.add_argument("--voxel-backend", choices=["auto", "pyrender", "open3d"], default="auto",
                   help="voxelisation du voxel_skin (auto : pyrender/EGL puis repli open3d)")
    p.add_argument("--skeleton-ckpt", default=None)
    p.add_argument("--skin-ckpt", default=None)
    p.add_argument("--workdir", default=None,
                   help="si fourni : ecrit raw_data.npz / predict_skeleton.npz / "
                        "predict_skin.npz (formats UniRig, pour debug)")
    p.add_argument("--device", default="cuda")
    return p.parse_args()


def resolve_root(cli_root):
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (cli_root, os.environ.get("UNIRIG_ROOT"), os.getcwd(), here,
              os.path.expanduser("~/UniRig")):
        if c and os.path.isfile(os.path.join(c, "run.py")) and \
                os.path.isdir(os.path.join(c, "src", "model")):
            return os.path.abspath(c)
    raise SystemExit("racine UniRig introuvable (utiliser --unirig-root ou UNIRIG_ROOT)")


def git_commit(root):
    try:
        head = open(os.path.join(root, ".git", "HEAD")).read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1]
            p = os.path.join(root, ".git", ref)
            if os.path.exists(p):
                return open(p).read().strip()
            packed = os.path.join(root, ".git", "packed-refs")
            for line in open(packed):
                if line.strip().endswith(ref):
                    return line.split()[0]
        return head
    except Exception:
        return "unknown"


def resolve_ckpt(explicit, key):
    if explicit:
        return os.path.abspath(explicit)
    local_rel, hf_name = CKPTS[key]
    if os.path.exists(local_rel):
        return os.path.abspath(local_rel)
    from huggingface_hub import hf_hub_download  # meme source que src/inference/download.py
    return hf_hub_download(repo_id="VAST-AI/UniRig", filename=hf_name)


def load_yaml(path):
    import yaml
    from box import Box
    with open(path, "r") as f:
        return Box(yaml.safe_load(f))


def cfg_path(kind, name):
    # meme logique que run.py load() : le nom de composant n'a pas l'extension .yaml
    name = name[:-5] if name.endswith(".yaml") else name
    return os.path.join("configs", kind, name + ".yaml")


# --------------------------------------------------------------------------------------------
# flash_attn : vrai module ou shim pur PyTorch
# --------------------------------------------------------------------------------------------
def _purge_flash_modules():
    for k in list(sys.modules):
        if k == "flash_attn" or k.startswith("flash_attn."):
            del sys.modules[k]


def install_flash_attn_shim():
    """Fournit flash_attn.modules.mha.MHA en pur PyTorch.

    src/model/unirig_skin.py : MHA(embed_dim=feat_dim, num_heads=num_heads, cross_attn=True),
    use_flash_attn laisse a False -> dans flash_attn 2.8.3 (flash_attn/modules/mha.py) le calcul
    passe par CrossAttention (einsum + softmax), reproduit ici a l'identique, avec les memes noms
    de parametres que le checkpoint : Wq, Wkv, out_proj.
    """
    import torch
    import torch.nn as nn

    class MHA(nn.Module):
        def __init__(self, embed_dim, num_heads, num_heads_kv=None, cross_attn=False,
                     qkv_proj_bias=True, out_proj_bias=True, dropout=0.0, softmax_scale=None,
                     causal=False, use_flash_attn=False, device=None, dtype=None, **kwargs):
            super().__init__()
            if (not cross_attn) or causal or use_flash_attn or kwargs.get("rotary_emb_dim", 0) \
                    or kwargs.get("dwconv", False) or kwargs.get("return_residual", False):
                raise NotImplementedError("shim MHA : seul cross_attn=True non causal est gere")
            fk = {"device": device, "dtype": dtype}
            self.embed_dim = embed_dim
            self.num_heads = num_heads
            self.num_heads_kv = num_heads_kv if num_heads_kv is not None else num_heads
            assert embed_dim % num_heads == 0 and num_heads % self.num_heads_kv == 0
            self.head_dim = embed_dim // num_heads
            self.softmax_scale = softmax_scale
            self.drop = nn.Dropout(dropout)
            self.Wq = nn.Linear(embed_dim, embed_dim, bias=qkv_proj_bias, **fk)
            self.Wkv = nn.Linear(embed_dim, 2 * self.head_dim * self.num_heads_kv,
                                 bias=qkv_proj_bias, **fk)
            self.out_proj = nn.Linear(embed_dim, embed_dim, bias=out_proj_bias, **fk)

        def forward(self, x, x_kv=None, key_padding_mask=None, **kwargs):
            q = self.Wq(x)
            kv = self.Wkv(x if x_kv is None else x_kv)
            q = q.view(*q.shape[:-1], self.num_heads, self.head_dim)            # (h d)
            kv = kv.view(*kv.shape[:-1], 2, self.num_heads_kv, self.head_dim)   # (two hkv d)
            if self.num_heads_kv != self.num_heads:
                kv = kv.repeat_interleave(self.num_heads // self.num_heads_kv, dim=-2)
            k, v = kv.unbind(dim=2)
            scale = self.softmax_scale or 1.0 / math.sqrt(q.shape[-1])
            scores = torch.einsum("bthd,bshd->bhts", q, k * scale)
            if key_padding_mask is not None:
                pad = torch.full(key_padding_mask.shape, -10000.0, dtype=scores.dtype,
                                 device=scores.device)
                pad.masked_fill_(key_padding_mask, 0.0)
                scores = scores + pad[:, None, None, :]
            attn = self.drop(torch.softmax(scores, dim=-1, dtype=v.dtype))
            out = torch.einsum("bhts,bshd->bthd", attn, v)
            return self.out_proj(out.reshape(*out.shape[:-2], self.embed_dim))

    def _mod(name, is_pkg):
        m = types.ModuleType(name)
        m.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=is_pkg)
        if is_pkg:
            m.__path__ = []
        return m

    pkg = _mod("flash_attn", True)
    pkg.__version__ = "0.0.0+unirig_entry_shim"
    sub = _mod("flash_attn.modules", True)
    mha = _mod("flash_attn.modules.mha", False)
    mha.MHA = MHA
    pkg.modules = sub
    sub.mha = mha
    sys.modules.update({"flash_attn": pkg, "flash_attn.modules": sub,
                        "flash_attn.modules.mha": mha})


def setup_attention(mode):
    # transformers fige sa detection de flash_attn a l'import : l'importer AVANT un shim
    import transformers  # noqa: F401
    if mode in ("auto", "flash"):
        try:
            import flash_attn  # noqa: F401
            from flash_attn import flash_attn_varlen_qkvpacked_func  # noqa: F401 (extension CUDA)
            from flash_attn.modules.mha import MHA  # noqa: F401
            return "flash"
        except Exception as e:  # ImportError, OSError (lib CUDA)...
            if mode == "flash":
                raise
            log(f"flash_attn indisponible ({type(e).__name__}: {e}) -> sdpa + shim MHA")
            _purge_flash_modules()
    install_flash_attn_shim()
    return "sdpa"


# --------------------------------------------------------------------------------------------
# outils geometrie
# --------------------------------------------------------------------------------------------
def apply_T(T, v):
    # meme convention que Augment._apply (src/data/augment.py)
    return v @ T[:3, :3].T + T[:3, 3]


def merge_positions(V, decimals=8):
    """Fusion des sommets de meme position (equivalent trimesh merge_vertices, tol.merge=1e-8).
    Renvoie (V_unique, inverse) avec V[i] == V_unique[inverse[i]]."""
    key = np.round(np.asarray(V, dtype=np.float64), decimals)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return np.asarray(V, dtype=np.float64)[first], inverse.reshape(-1)


def seed_all(seed):
    import lightning as L
    L.seed_everything(seed, workers=True)  # comme run.py


def autocast_ctx(precision, device):
    import torch
    if str(device).startswith("cuda"):
        if str(precision).startswith("bf16"):
            return torch.autocast("cuda", dtype=torch.bfloat16)
        if str(precision).startswith("16"):
            return torch.autocast("cuda", dtype=torch.float16)
    return contextlib.nullcontext()


def to_device(batch, device):
    import torch
    return {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in batch.items()}


# --------------------------------------------------------------------------------------------
# construction modele / systeme (equivalent run.py, sans Trainer ni writers bpy)
# --------------------------------------------------------------------------------------------
def build_system(task, attn_mode, tokenizer, ckpt_path, device):
    import torch
    from src.model.parse import get_model
    from src.system.parse import get_system

    model_cfg = load_yaml(cfg_path("model", task.components.model))
    if attn_mode != "flash":
        if "llm" in model_cfg:
            model_cfg.llm["_attn_implementation"] = "sdpa"
        if model_cfg.get("mesh_encoder", {}).get("__target__") == "ptv3obj":
            model_cfg.mesh_encoder["enable_flash"] = False
    model = get_model(tokenizer=tokenizer, **model_cfg)

    system_cfg = load_yaml(cfg_path("system", task.components.system))
    system = get_system(**system_cfg, model=model, optimizer_config=None, loss_config=None,
                        scheduler_config=None, steps_per_epoch=1)

    # checkpoint Lightning officiel (HF VAST-AI/UniRig) ; weights_only=False car il contient
    # des hyperparametres/etats de boucle Lightning (source de confiance).
    ck = torch.load(ckpt_path, map_location="cpu", mmap=True, weights_only=False)
    system.load_state_dict(ck["state_dict"], strict=True)
    del ck
    system.to(device)
    system.eval()
    return system


def make_model_input(raw, cls, path, data_name, transform_config, pad):
    """Equivalent de UniRigDataset.__getitem__ (src/data/dataset.py), en gardant la matrice
    de normalisation AugmentAffine."""
    from src.data.asset import Asset
    from src.data.transform import transform_asset
    from src.data.augment import AugmentAffine
    from src.model.spec import ModelInput

    asset = Asset.from_raw_data(raw_data=raw, cls=cls, path=path, data_name=data_name)
    first, _second = transform_asset(asset=asset, transform_config=transform_config)
    aff = [a for a in first if isinstance(a, AugmentAffine)]
    assert len(aff) == 1, "AugmentAffine attendu une fois dans la config de transformation"
    T = np.array(aff[0].trans_vertex, dtype=np.float64)
    mi = ModelInput(
        tokens=None,
        pad=pad,
        vertices=asset.sampled_vertices.astype(np.float32),
        normals=asset.sampled_normals.astype(np.float32),
        joints=None if asset.joints is None else asset.joints.astype(np.float32),
        tails=None if asset.tails is None else asset.tails.astype(np.float32),
        asset=asset,
        augments=None,
    )
    return asset, mi, T


def preprocess_like_extract(V_bl, F, target_count):
    """save_raw_data() de src/data/extract.py, sans bpy."""
    import trimesh
    import fast_simplification
    from src.data.raw_data import RawData

    mesh = trimesh.Trimesh(vertices=V_bl, faces=F)  # process=True : fusion des sommets
    v = np.array(mesh.vertices, dtype=np.float32)
    f = np.array(mesh.faces, dtype=np.int64)
    if f.shape[0] > target_count:
        v, f = fast_simplification.simplify(v, f, target_count=target_count)
    mesh = trimesh.Trimesh(vertices=v, faces=f)
    raw = RawData(
        vertices=np.array(mesh.vertices, dtype=np.float32),
        vertex_normals=np.array(mesh.vertex_normals, dtype=np.float32),
        faces=np.array(mesh.faces, dtype=np.int64),
        face_normals=np.array(mesh.face_normals, dtype=np.float32),
        joints=None, tails=None, skin=None, no_skin=None, parents=None, names=None,
        matrix_local=None,
    )
    raw.check()
    return raw


# --------------------------------------------------------------------------------------------
# etapes
# --------------------------------------------------------------------------------------------
def stage_skeleton(args, raw, attn_mode, device, workdir):
    import torch
    from torch.utils.data import default_collate
    from src.data.transform import TransformConfig
    from src.tokenizer.spec import TokenizerConfig
    from src.tokenizer.parse import get_tokenizer

    task = load_yaml(SKELETON_TASK)
    tokenizer = get_tokenizer(config=TokenizerConfig.parse(
        config=load_yaml(cfg_path("tokenizer", task.components.tokenizer))))
    tr_cfg = TransformConfig.parse(
        config=load_yaml(cfg_path("transform", task.components.transform)).predict_transform_config)
    ckpt = resolve_ckpt(args.skeleton_ckpt, "skeleton")
    log(f"squelette : checkpoint {ckpt}")

    # run.py : seed_everything puis construction du modele puis echantillonnage (dataset) puis
    # generation ; on garde cet ordre pour le 1er essai, on re-seme seulement pour les essais
    # suivants (equivalent de relancer generate_skeleton.sh avec --seed seed+n).
    seed_all(args.seed)
    system = build_system(task, attn_mode, tokenizer, ckpt, device)
    if args.cls:
        system.generate_kwargs["assign_cls"] = args.cls
    precision = task.get("trainer", {}).get("precision", "bf16-mixed")

    last_err = None
    res = None
    seed = args.seed
    for attempt in range(max(0, args.skeleton_retries) + 1):
        seed = args.seed + attempt
        if attempt > 0:
            seed_all(seed)
        asset, mi, T1 = make_model_input(raw, cls="inference", path=workdir,
                                         data_name="raw_data.npz", transform_config=tr_cfg,
                                         pad=tokenizer.pad)
        batch = to_device(default_collate(system.model._process_fn([mi])), device)
        try:
            with torch.inference_mode(), autocast_ctx(precision, device):
                # ARSystem.predict_step avale les exceptions ; _predict_step les laisse passer
                preds = system._predict_step(batch=batch, batch_idx=0)
            if not preds:
                raise RuntimeError("aucune prediction")
            res = preds[0]
            if res.joints.shape[0] < 2:
                raise RuntimeError(f"squelette degenere ({res.joints.shape[0]} joint)")
            log(f"squelette : {res.joints.shape[0]} joints (graine {seed}, classe {res.cls})")
            break
        except Exception as e:  # ex. ValueError 'last token is not eos' (max_new_tokens atteint)
            last_err = e
            log(f"squelette : echec graine {seed} : {type(e).__name__}: {e}")
            res = None
    del system
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    if res is None:
        raise RuntimeError(f"generation du squelette impossible : {last_err}")
    return asset, res, T1, seed


def stage_skin(args, asset1, res, attn_mode, device, workdir):
    import torch
    from torch.utils.data import default_collate
    from src.data.raw_data import RawData
    from src.data.transform import TransformConfig

    task = load_yaml(SKIN_TASK)
    ckpt = resolve_ckpt(args.skin_ckpt, "skin")
    log(f"skin : checkpoint {ckpt}")
    seed_all(args.seed)  # generate_skin.sh relance run.py avec la meme graine
    system = build_system(task, attn_mode, None, ckpt, device)
    precision = task.get("trainer", {}).get("precision", "bf16-mixed")

    def make_raw2():
        # equivalent du predict_skeleton.npz ecrit par ARWriter (src/system/ar.py) :
        # maillage deja normalise par l'etape squelette + joints predits.
        return RawData(
            vertices=asset1.vertices.astype(np.float32).copy(),
            vertex_normals=asset1.vertex_normals.astype(np.float32).copy(),
            faces=asset1.faces.astype(np.int64).copy(),
            face_normals=asset1.face_normals.astype(np.float32).copy(),
            joints=np.asarray(res.joints, dtype=np.float32).copy(),
            tails=np.asarray(res.tails, dtype=np.float32).copy(),
            skin=None, no_skin=None,
            parents=list(res.parents), names=list(res.names),
            matrix_local=None, path=None, cls=res.cls,
        )

    backends = ["pyrender", "open3d"] if args.voxel_backend == "auto" else [args.voxel_backend]
    tr_box = load_yaml(cfg_path("transform", task.components.transform)).predict_transform_config
    asset2 = mi2 = T2 = None
    used_backend = None
    for i, backend in enumerate(backends):
        tr_box.vertex_group_config.kwargs.voxel_skin["backend"] = backend
        tr_cfg = TransformConfig.parse(config=tr_box)
        if i > 0:
            seed_all(args.seed)  # repli : meme echantillonnage que le 1er essai
        try:
            asset2, mi2, T2 = make_model_input(make_raw2(), cls="inference", path=workdir,
                                               data_name="predict_skeleton.npz",
                                               transform_config=tr_cfg, pad=None)
            used_backend = backend
            break
        except Exception as e:
            log(f"voxel_skin backend {backend} en echec : {type(e).__name__}: {e}")
            if i == len(backends) - 1:
                raise
    assert list(asset2.names) == list(res.names), "ordre des joints modifie a l'etape skin"

    batch = to_device(default_collate(system.model._process_fn([mi2])), device)
    with torch.inference_mode(), autocast_ctx(precision, device):
        out = system.predict_step(batch, 0)
    skin_pred = out["skin_pred"][0]
    skin_pred = skin_pred.float().detach().cpu().numpy() if torch.is_tensor(skin_pred) \
        else np.asarray(skin_pred, dtype=np.float32)
    sampled = out.get("sampled_vertices", None)
    if sampled is None:
        sampled = batch["vertices"][0]
    sampled = sampled.float().detach().cpu().numpy() if torch.is_tensor(sampled) \
        else np.asarray(sampled, dtype=np.float32)
    del system
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    log(f"skin : prediction {skin_pred.shape} sur points echantillonnes (backend {used_backend})")
    return asset2, skin_pred, sampled, T2, used_backend


# --------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------
def main():
    args = parse_args()
    t0 = time.time()
    mesh_path = os.path.abspath(args.mesh)
    out_path = os.path.abspath(args.out)
    workdir = os.path.abspath(args.workdir) if args.workdir else None

    root = resolve_root(args.unirig_root)
    os.chdir(root)  # les configs UniRig contiennent des chemins relatifs (./configs/skeleton/...)
    if root not in sys.path:
        sys.path.insert(0, root)
    log(f"UniRig : {root} (commit {git_commit(root)[:12]})")

    # ---- entree
    data = np.load(mesh_path)
    V_in = np.asarray(data["vertices"], dtype=np.float64)
    F_in = np.asarray(data["faces"], dtype=np.int64)
    if V_in.ndim != 2 or V_in.shape[1] != 3 or F_in.ndim != 2 or F_in.shape[1] != 3:
        raise SystemExit(f"formes invalides : vertices {V_in.shape}, faces {F_in.shape}")
    if F_in.size == 0 or F_in.min() < 0 or F_in.max() >= V_in.shape[0]:
        raise SystemExit("indices de faces hors bornes")
    if not np.isfinite(V_in).all():
        raise SystemExit("vertices non finis")
    log(f"entree : {V_in.shape[0]} sommets, {F_in.shape[0]} faces")

    # cumm (spconv) decide a l'import s'il est "CPU only" selon que `nvcc` est dans le PATH
    # (cumm/constants.py) : on ajoute le CUDA toolkit au PATH si besoin (ssh non interactif).
    for cuda_bin in (os.path.join(os.environ.get("CUDA_HOME", "/nonexistent"), "bin"),
                     "/usr/local/cuda/bin"):
        if os.path.isfile(os.path.join(cuda_bin, "nvcc")):
            if cuda_bin not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
            os.environ.setdefault("CUDA_HOME", os.path.dirname(cuda_bin))
            break

    import torch
    torch.set_float32_matmul_precision("high")  # comme run.py
    device = args.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise SystemExit("CUDA indisponible (spconv / flash_attn exigent le GPU)")

    attn_mode = setup_attention(args.attn)
    log(f"attention : {attn_mode}")

    # ---- 1. repere + pre-traitement (remplace extract.py / bpy)
    V_bl = V_in @ R_IN_TO_BL.T
    raw = preprocess_like_extract(V_bl, F_in, args.faces_target)
    log(f"pre-traitement : {raw.vertices.shape[0]} sommets, {raw.faces.shape[0]} faces "
        f"(decimation si > {args.faces_target})")
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        raw.save(os.path.join(workdir, "raw_data.npz"))

    # ---- 2. squelette
    asset1, res, T1, seed_used = stage_skeleton(args, raw, attn_mode, device, workdir or root)
    if workdir:
        from src.data.raw_data import RawData
        RawData(vertices=asset1.vertices, vertex_normals=asset1.vertex_normals,
                faces=asset1.faces, face_normals=asset1.face_normals, joints=res.joints,
                tails=res.tails, parents=res.parents, skin=None, no_skin=res.no_skin,
                names=res.names, matrix_local=None, path=None, cls=res.cls
                ).save(os.path.join(workdir, "predict_skeleton.npz"))

    # ---- 3. skin
    asset2, skin_pred, sampled, T2, backend = stage_skin(args, asset1, res, attn_mode, device,
                                                         workdir or root)
    if workdir:
        from src.data.raw_data import RawSkin
        RawSkin(skin=skin_pred, vertices=sampled, joints=asset2.joints).save(
            os.path.join(workdir, "predict_skin.npz"))

    # ---- 4. report des poids sur les sommets d'entree (fusionnes) avec reskin() d'UniRig
    from src.system.skin import reskin
    J = int(skin_pred.shape[1])
    parents_list = [None if p is None or int(p) < 0 else int(p) for p in asset2.parents]
    V_m, inv = merge_positions(V_in)
    F_m = inv[F_in]
    V_m_n2 = apply_T(T2, apply_T(T1, V_m @ R_IN_TO_BL.T))
    # float32 comme dans SkinWriter (sampled_vertices / skin_pred / origin_vertices)
    skin_m = reskin(sampled_vertices=sampled.astype(np.float32),
                    vertices=V_m_n2.astype(np.float32), parents=parents_list, faces=F_m,
                    sampled_skin=skin_pred.astype(np.float32), **RESKIN_KWARGS)
    skin_m = np.nan_to_num(np.asarray(skin_m, dtype=np.float64), nan=0.0, posinf=0.0,
                           neginf=0.0)
    bad = ~(skin_m.sum(axis=1) > 1e-8)
    if bad.any():
        # repli : joint le plus proche (repere normalise de l'etape skin)
        d = np.linalg.norm(V_m_n2[bad][:, None, :] - asset2.joints[None, :, :], axis=-1)
        skin_m[bad] = 0.0
        skin_m[np.where(bad)[0], d.argmin(axis=1)] = 1.0
        log(f"reskin : {int(bad.sum())} sommets sans poids -> joint le plus proche")
    skin_full = skin_m[inv]  # (V_in, J)

    k = max(1, min(args.topk, J))
    idx = np.argsort(-skin_full, axis=1, kind="stable")[:, :k]
    w = np.take_along_axis(skin_full, idx, axis=1)
    w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)

    # ---- 5. joints -> repere d'entree
    T1_inv = np.linalg.inv(T1)
    joints_in = apply_T(T1_inv, np.asarray(res.joints, dtype=np.float64)) @ R_IN_TO_BL
    tails_in = apply_T(T1_inv, np.asarray(res.tails, dtype=np.float64)) @ R_IN_TO_BL
    parents = np.array([-1 if p is None else int(p) for p in res.parents], dtype=np.int32)
    assert parents[0] == -1 and all(parents[i] < i for i in range(1, len(parents)))
    names = [str(n) for n in res.names]

    meta = {
        "unirig_commit": git_commit(root),
        "seed": int(seed_used),
        "cls": res.cls,
        "attention": attn_mode,
        "voxel_backend": backend,
        "n_vertices_in": int(V_in.shape[0]),
        "n_vertices_merged": int(V_m.shape[0]),
        "n_vertices_model": int(raw.vertices.shape[0]),
        "n_faces_model": int(raw.faces.shape[0]),
        "n_joints": int(J),
        "topk": int(k),
        "axes": "entree Y haut/+Z avant ; UniRig Z haut/-Y avant ; v_bl = R @ v_in",
        "R_in_to_blender": R_IN_TO_BL.tolist(),
        "T1_blender_to_norm_skeleton": T1.tolist(),
        "T2_norm_skeleton_to_norm_skin": T2.tolist(),
        "reskin": RESKIN_KWARGS,
        "skin_transfer": "reskin() UniRig sur sommets d'entree fusionnes par position",
        "seconds": round(time.time() - t0, 2),
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        np.savez(
            f,
            joint_names=np.array(names, dtype=str),
            parents=parents,
            joint_positions=joints_in.astype(np.float32),
            skin_joints=idx.astype(np.int32),
            skin_weights=w.astype(np.float32),
            joint_tails=tails_in.astype(np.float32),
            meta_json=np.array(json.dumps(meta, ensure_ascii=False)),
        )
    os.replace(tmp, out_path)
    log(f"ecrit {out_path} : J={J}, V={V_in.shape[0]}, k={k}, {meta['seconds']} s")


if __name__ == "__main__":
    main()
