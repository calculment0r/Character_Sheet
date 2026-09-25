"""L'étage 3D : deux moteurs derrière une même interface (§7.1).

    generate(engine, views, style, options) -> dossier de version

TRELLIS 2 est le moteur par défaut : licence MIT, aucune restriction.
Hunyuan3D 2.1 texture mieux mais sa licence communautaire exclut l'UE,
le Royaume-Uni et la Corée du Sud — à lire ligne à ligne avant tout
usage depuis Paris. Les deux restent disponibles, au choix de chaque
génération.

Chaque version sort en GLB unique, textures embarquées (§7.3), et ses
canaux PBR sont aussi rangés à part — albedo, metallic, roughness,
normal — pour l'inspection.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, gltf, stubs3d

# Le nom de moteur exposé → la capacité qui le porte.
ENGINES = {"trellis2": "trellis", "hunyuan3d-2.1": "hunyuan3d"}
DEFAULT_ENGINE = "trellis2"
LICENSE_NOTE = {
    "hunyuan3d-2.1": "licence Hunyuan3D 2.1 : exclusion UE / Royaume-Uni / Corée du Sud — à lire avant production",
}


def generate(engine: str, *, views: dict[str, Path], out_dir: Path, seed: int, style: str = "photoreal",
             single_view: Path | None = None, texture: bool = True, height_m: float = 1.75,
             report=lambda p, m: None) -> dict:
    if engine not in ENGINES:
        raise ValueError(f"moteur 3D inconnu : {engine} (possibles : {', '.join(ENGINES)})")
    backend = config.backend(ENGINES[engine])
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / "model.glb"
    extra = {}

    if backend == "stub":
        report(0.3, f"factice · {engine}")
        _stub(glb_path, seed=seed + (0 if engine == "trellis2" else 1))
    elif backend == "comfyui":
        from . import mesh_comfy

        extra = mesh_comfy.generate(engine, views=views, single_view=single_view, dest=glb_path, seed=seed,
                                    texture=texture, height_m=height_m, report=report)
    elif engine == "trellis2":
        from . import mesh_trellis

        mesh_trellis.generate(views=views, single_view=single_view, dest=glb_path, seed=seed,
                              texture=texture, report=report)
    else:
        from . import mesh_hunyuan

        mesh_hunyuan.generate(views=views, single_view=single_view, dest=glb_path, seed=seed,
                              texture=texture, report=report)

    report(0.9, "canaux PBR")
    maps = extract_maps(glb_path, out_dir)
    stats = glb_stats(glb_path)
    meta = {"engine": engine, "backend": backend, "seed": seed, "style": style, "height_m": height_m,
            "single_view": bool(single_view), "stats": stats, "maps": sorted(maps), **extra}
    (out_dir / "mesh.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"glb": glb_path, "maps": maps, "stats": stats, "backend": backend}


def _stub(dest: Path, *, seed: int) -> None:
    m = stubs3d.mannequin_mesh()
    maps = stubs3d.pbr_maps(seed)
    g = gltf.GLB()
    mat = g.material("factice", base_color=stubs3d.png(maps["albedo"]),
                     metallic_roughness=stubs3d.png(stubs3d.packed_metallic_roughness(maps)),
                     normal=stubs3d.png(maps["normal"]))
    mesh = g.mesh("mannequin", positions=m["positions"], normals=m["normals"], uvs=m["uvs"],
                  indices=m["indices"], material=mat)
    g.node("mannequin", mesh=mesh, root=True)
    g.doc["asset"]["extras"] = {"factice": True}
    g.save(dest)


# ── inspection ─────────────────────────────────────────────────────

def _image_bytes(g: gltf.GLB, texture_index: int) -> bytes | None:
    try:
        tex = g.doc["textures"][texture_index]
        img = g.doc["images"][tex["source"]]
        view = g.doc["bufferViews"][img["bufferView"]]
    except (KeyError, IndexError):
        return None
    start = view.get("byteOffset", 0)
    return bytes(g.bin[start: start + view["byteLength"]])


def extract_maps(glb_path: Path, out_dir: Path) -> dict[str, Path]:
    """Sort les canaux PBR du premier matériau texturé. glTF range la
    rugosité dans le vert et le métal dans le bleu d'une même texture ;
    on les sépare pour les lire chacun."""
    g = gltf.GLB.load(glb_path)
    out: dict[str, Path] = {}
    for mat in g.doc.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        if "baseColorTexture" in pbr and (data := _image_bytes(g, pbr["baseColorTexture"]["index"])):
            Image.open(io.BytesIO(data)).convert("RGB").save(out_dir / "albedo.png")
            out["albedo"] = out_dir / "albedo.png"
        if "metallicRoughnessTexture" in pbr and (data := _image_bytes(g, pbr["metallicRoughnessTexture"]["index"])):
            mr = Image.open(io.BytesIO(data)).convert("RGB")
            _, rough, metal = mr.split()
            rough.save(out_dir / "roughness.png")
            metal.save(out_dir / "metallic.png")
            out["roughness"], out["metallic"] = out_dir / "roughness.png", out_dir / "metallic.png"
        if "normalTexture" in mat and (data := _image_bytes(g, mat["normalTexture"]["index"])):
            Image.open(io.BytesIO(data)).convert("RGB").save(out_dir / "normal.png")
            out["normal"] = out_dir / "normal.png"
        if out:
            break
    return out


def glb_stats(glb_path: Path) -> dict:
    g = gltf.GLB.load(glb_path)
    verts = tris = 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for mesh in g.doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            acc = g.doc["accessors"][prim["attributes"]["POSITION"]]
            verts += acc["count"]
            if "min" in acc:
                lo, hi = np.minimum(lo, acc["min"]), np.maximum(hi, acc["max"])
            if "indices" in prim:
                tris += g.doc["accessors"][prim["indices"]]["count"] // 3
            else:
                tris += acc["count"] // 3
    size = (hi - lo).tolist() if np.isfinite(lo).all() else None
    return {"vertices": int(verts), "triangles": int(tris), "size_m": [round(x, 4) for x in size] if size else None,
            "materials": len(g.doc.get("materials", [])), "textures": len(g.doc.get("textures", []))}
