"""Planche de contrôle d'un ou plusieurs GLB, sans écran : le lanceur de
rayons BVH de ComfyUI (comfy_extras.nodes_mesh_postprocess), albedo sans
éclairage puis argile, sous plusieurs azimuts. C'est ainsi que les quatre
bras d'essai-atelier ont été vus et comparés (docs/ETUDES.md §4.1).

À lancer avec le python de ComfyUI (torch, trimesh) :
  ~/ComfyUI/venv/bin/python tools/remote/mesh_render.py planche.jpg \
      mesh/v001/model.glb:v1 mesh/v002/model.glb:v2 [--az 0,45,90,...] [--size 384] [--noclay]
Lignes = meshes ; colonnes = azimuts (0 face, 90 flanc gauche, +X).
Conventions : Y en haut, le personnage regarde +Z.
"""
import math
import sys

sys.path.insert(0, str(__import__("pathlib").Path.home() / "ComfyUI"))
import numpy as np
import torch
import trimesh
from PIL import Image, ImageDraw

from comfy_extras import nodes_mesh_postprocess as mp

args = sys.argv[1:]
az = [0, 45, 90, 135, 180, 225, 270, 315]
size = 384
clay = True
if "--az" in args:
    i = args.index("--az"); az = [float(x) for x in args[i + 1].split(",")]; del args[i:i + 2]
if "--size" in args:
    i = args.index("--size"); size = int(args[i + 1]); del args[i:i + 2]
if "--noclay" in args:
    args.remove("--noclay"); clay = False
out, items = args[0], args[1:]
dev = torch.device("cuda")


def load(path):
    scene = trimesh.load(path, force="scene", process=False)
    vs, fs, uvs, texs = [], [], [], []
    off = 0
    for geom in scene.geometry.values():
        v = np.asarray(geom.vertices, dtype=np.float32)
        f = np.asarray(geom.faces, dtype=np.int64)
        vs.append(v); fs.append(f + off); off += len(v)
        uv = getattr(geom.visual, "uv", None)
        uvs.append(np.asarray(uv, dtype=np.float32) if uv is not None else np.zeros((len(v), 2), np.float32))
        img = None
        mat = getattr(geom.visual, "material", None)
        if mat is not None:
            img = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        texs.append(img)
    v = np.concatenate(vs); f = np.concatenate(fs); uv = np.concatenate(uvs)
    tex = texs[0]
    return v, f, uv, tex


rows = []
for item in items:
    path, _, label = item.partition(":")
    label = label or path.split("/")[-2]
    v, f, uv, tex = load(path)
    lo, hi = v.min(0), v.max(0)
    center = (lo + hi) / 2
    height = hi[1] - lo[1]
    V = torch.from_numpy(v).to(dev)
    F = torch.from_numpy(f).to(dev)
    tri = V[F]
    bvh = mp._build_triangle_bvh(tri)
    UV = torch.from_numpy(uv).to(dev)
    UV = torch.stack([UV[:, 0], 1.0 - UV[:, 1]], -1)  # glTF uv (0,0)=top-left of image; sampler wants v down
    T = None
    if tex is not None:
        T = torch.from_numpy(np.asarray(tex.convert("RGB"), dtype=np.float32) / 255.0).to(dev)
    fov = math.radians(20)
    dist = 1.1 * height * 0.5 / math.tan(fov / 2) * 1.05
    cells = []
    for a in az:
        ar = math.radians(a)
        eye = torch.tensor([center[0] + dist * math.sin(ar), center[1], center[2] + dist * math.cos(ar)], device=dev,
                           dtype=torch.float32)
        c = torch.tensor(center, device=dev, dtype=torch.float32)
        fw, r, u = mp._camera_basis(eye, c, torch.tensor([0.0, 1.0, 0.0], device=dev))
        H, W = size, int(size * 0.75)
        views = []
        if T is not None:
            img, hit, _ = mp._render_view(tri, bvh, UV, F, T, eye, fw, r, u, fov, H, W)
            img = torch.where(hit[..., None], img, torch.full_like(img, 0.5))
            views.append(img)
        if clay:
            img, hit, _ = mp._render_view(tri, bvh, None, F, None, eye, fw, r, u, fov, H, W)
            img = torch.where(hit[..., None], img, torch.full_like(img, 0.5))
            views.append(img)
        cells.append(torch.cat(views, 0))
    row = torch.cat(cells, 1).clamp(0, 1).mul(255).byte().cpu().numpy()
    im = Image.fromarray(row)
    d = ImageDraw.Draw(im)
    d.text((6, 4), label, fill=(255, 255, 0))
    for k, a in enumerate(az):
        d.text((k * int(size * 0.75) + 6, 18), f"{int(a)}", fill=(255, 255, 255))
    rows.append(im)
    print(label, "verts", len(v), "faces", len(f), "extent", (hi - lo).round(3), flush=True)
Wd = max(r.width for r in rows)
sheet = Image.new("RGB", (Wd, sum(r.height for r in rows)), (40, 40, 40))
y = 0
for r in rows:
    sheet.paste(r, (0, y)); y += r.height
sheet.save(out, quality=88)
print("wrote", out)
