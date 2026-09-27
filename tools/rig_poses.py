"""Les cinq poses de contrôle d'un rig, rejouées et rendues sans GPU.

    python3 tools/rig_poses.py <rigged.glb> <dossier>       # numpy et Pillow suffisent
    python3 tools/rig_poses.py <model.glb> <dossier> --mesh # un mesh seul, de face et de profil

Rejoue le skinning glTF comme un viewer (le même code que
`chain_check.py`), pour le bind et chaque clip `control/…`, et écrit :

  - `<pose>.png` : de face et de profil gauche, le mesh ombré et le
    squelette par-dessus ;
  - `planche.png` : toutes les poses côte à côte ;
  - `poids.png` : le bind, chaque sommet teint par son articulation
    dominante (une zone mal attribuée se voit d'un coup d'œil) ;
  - `poses.json` : les chiffres — mains contre tête, hanches, pieds, et
    l'étirement des arêtes du mesh (longueur posée / longueur au bind) :
    un sommet lié au mauvais os étire ses arêtes bien au-delà de ×2.

Le rendu est orthographique, à l'échelle fixe (une même fenêtre de
2,4 × 2,5 m pour toutes les poses), par l'algorithme du peintre.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from chain_check import clip_pose, skinned  # noqa: E402
from factory.gltf import GLB  # noqa: E402

WINDOW = (-1.2, 1.2, -0.12, 2.38)       # x min, x max, y min, y max, en mètres
PX_PER_M = 200


def faces_of(g: GLB) -> np.ndarray:
    out, base = [], 0
    for mesh in g.doc["meshes"]:
        for prim in mesh["primitives"]:
            n = g.doc["accessors"][prim["attributes"]["POSITION"]]["count"]
            f = (g.read_accessor(prim["indices"]).reshape(-1, 3) if "indices" in prim
                 else np.arange(n).reshape(-1, 3))
            out.append(f.astype(np.int64) + base)
            base += n
    return np.vstack(out)


def bones_of(g: GLB) -> list[tuple[str, str]]:
    nodes = g.doc["nodes"]
    joints = set(g.doc["skins"][0]["joints"]) if g.doc.get("skins") else set()
    return [(nodes[p].get("name", str(p)), nodes[c].get("name", str(c)))
            for p in joints for c in nodes[p].get("children", []) if c in joints]


def _project(v: np.ndarray, view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(u, v) en pixels et profondeur (plus grand = plus près)."""
    x0, x1, y0, y1 = WINDOW
    if view == "front":            # depuis +Z : la gauche du personnage (+X) à droite de l'image
        u, d = v[:, 0], v[:, 2]
    else:                          # depuis +X, son flanc gauche : l'avant (+Z) à gauche de l'image
        u, d = -v[:, 2], v[:, 0]
    px = (u - x0) * PX_PER_M
    py = (y1 - v[:, 1]) * PX_PER_M
    return px, py, d


def render(verts: np.ndarray, faces: np.ndarray, view: str, *, colors: np.ndarray | None = None,
           joints: dict[str, np.ndarray] | None = None, bones=(), title: str = "") -> Image.Image:
    x0, x1, y0, y1 = WINDOW
    w, h = int((x1 - x0) * PX_PER_M), int((y1 - y0) * PX_PER_M)
    img = Image.new("RGB", (w, h), (24, 24, 28))
    dr = ImageDraw.Draw(img)
    gy = (y1 - 0.0) * PX_PER_M
    dr.line([(0, gy), (w, gy)], fill=(70, 70, 80))
    px, py, d = _project(verts, view)
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    light = np.array([0.3, 0.5, 1.0]) if view == "front" else np.array([1.0, 0.5, -0.3])
    light /= np.linalg.norm(light)
    shade = 0.25 + 0.75 * np.abs(n @ light)
    base = (np.full((len(faces), 3), 200.0) if colors is None else colors[faces[:, 0]].astype(float))
    fill = np.clip(base * shade[:, None], 0, 255).astype(np.uint8)
    order = np.argsort(d[faces].mean(axis=1))
    fu, fv = px[faces], py[faces]
    for i in order:
        dr.polygon([(fu[i, 0], fv[i, 0]), (fu[i, 1], fv[i, 1]), (fu[i, 2], fv[i, 2])], fill=tuple(fill[i]))
    if joints:
        names = list(joints)
        jp = np.array([joints[k] for k in names])
        ju, jv, _ = _project(jp, view)
        at = {k: (ju[i], jv[i]) for i, k in enumerate(names)}
        for a, b in bones:
            if a in at and b in at:
                dr.line([at[a], at[b]], fill=(255, 140, 40), width=2)
        for k in names:
            u, v = at[k]
            dr.ellipse([u - 2, v - 2, u + 2, v + 2], fill=(255, 220, 120))
    if title:
        dr.text((8, 6), title, fill=(230, 230, 230))
    return img


def pair(verts, faces, **kw) -> Image.Image:
    a = render(verts, faces, "front", **kw)
    kw["title"] = ""
    b = render(verts, faces, "side", **kw)
    out = Image.new("RGB", (a.width + b.width, a.height))
    out.paste(a, (0, 0))
    out.paste(b, (a.width, 0))
    return out


def edge_stretch(bind: np.ndarray, posed: np.ndarray, faces: np.ndarray) -> dict:
    e = np.unique(np.sort(np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1), axis=0)
    lb = np.linalg.norm(bind[e[:, 0]] - bind[e[:, 1]], axis=1)
    keep = lb > 1e-5
    r = np.linalg.norm(posed[e[keep, 0]] - posed[e[keep, 1]], axis=1) / lb[keep]
    return {"p50": round(float(np.percentile(r, 50)), 3), "p99": round(float(np.percentile(r, 99)), 3),
            "max": round(float(r.max()), 2), "over_x2": int((r > 2).sum()), "over_x3": int((r > 3).sum()),
            "edges": int(keep.sum())}


def palette(k: int) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(60, 255, size=(k, 3))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--mesh", action="store_true", help="un mesh sans rig : de face et de profil seulement")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    g = GLB.load(a.glb)
    faces = faces_of(g)

    if a.mesh:
        v = np.vstack([g.read_accessor(p["attributes"]["POSITION"]).astype(float)
                       for m in g.doc["meshes"] for p in m["primitives"]])
        pair(v, faces, title=Path(a.glb).name).save(out / "mesh.png")
        print(json.dumps({"vertices": len(v), "faces": len(faces), "min": v.min(0).round(3).tolist(),
                          "max": v.max(0).round(3).tolist()}))
        return 0

    bones = bones_of(g)
    bind_v, bind_j = skinned(g, {})
    poses = {"bind": (bind_v, bind_j)}
    for anim in g.doc.get("animations", []):
        if anim["name"].startswith("control/"):
            poses[anim["name"].split("/", 1)[1]] = clip_pose(g, anim["name"])

    report = {}
    tiles = []
    for name, (v, j) in poses.items():
        img = pair(v, faces, joints=j, bones=bones, title=name)
        img.save(out / f"{name}.png")
        tiles.append(img)
        head = j.get("HeadEnd", j.get("Head"))
        report[name] = {
            "hands_y": [round(float(j["LeftHand"][1]), 3), round(float(j["RightHand"][1]), 3)],
            "head_top_y": round(float(head[1]), 3),
            "hips_y": round(float(j["Hips"][1]), 3),
            "feet_y": [round(float(j["LeftFoot"][1]), 3), round(float(j["RightFoot"][1]), 3)],
            "mesh_min_y": round(float(v[:, 1].min()), 3),
            "mesh_size": (v.max(0) - v.min(0)).round(3).tolist(),
            "stretch": edge_stretch(bind_v, v, faces),
        }
        print(name, json.dumps(report[name], ensure_ascii=False))

    sheet = Image.new("RGB", (sum(t.width for t in tiles[:3]), tiles[0].height * ((len(tiles) + 2) // 3)))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 3) * t.width, (i // 3) * t.height))
    sheet.save(out / "planche.png")

    # Le bind teint par articulation dominante.
    names = [g.doc["nodes"][k].get("name") for k in g.doc["skins"][0]["joints"]]
    dom = []
    for mesh in g.doc["meshes"]:
        for prim in mesh["primitives"]:
            jj = g.read_accessor(prim["attributes"]["JOINTS_0"]).astype(int)
            ww = g.read_accessor(prim["attributes"]["WEIGHTS_0"]).astype(float)
            dom.append(jj[np.arange(len(jj)), ww.argmax(axis=1)])
    dom = np.concatenate(dom)
    pair(bind_v, faces, colors=palette(len(names))[dom], title="poids : articulation dominante").save(out / "poids.png")
    used = {names[k]: int((dom == k).sum()) for k in np.unique(dom)}
    report["_dominant_joints"] = dict(sorted(used.items(), key=lambda x: -x[1]))
    (out / "poses.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("articulations dominantes :", json.dumps(report["_dominant_joints"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
