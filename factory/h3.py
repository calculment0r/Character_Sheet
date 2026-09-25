"""H3, en local, à 768 px de petit côté (arbitrage tranché par Cal).

Toutes les images de la chaîne passent par H3 et par la ruse des cinq
frames du §5.2 : on demande le minimum de frames, on garde la plus
nette, c'est l'image. Visage, plein pied, planche et vues ne diffèrent
que par le prompt, les références et le format.

Trois moteurs :
  - `comfyui` : H3 tourne déjà dans ComfyUI, qui le garde résident ; on
    lui confie `workflows/h3_ref2va.json` (ou `h3_orbit.json`) ;
  - `python`  : le code d'inférence MiniMax, importé dans ce processus ;
  - `stub`    : des images de test étiquetées FACTICE.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from . import config, imaging, prompts, stubs

# 768 de petit côté. Le nœud MiniMaxH3ReferenceToVideo prend largeur et
# hauteur par pas de 32 : 1344 × 768 est son format par défaut.
SIZES = {
    "face": (768, 768),
    "fullbody": (768, 1344),
    "sheet": (1344, 768),
    "view": (768, 1344),
    "orbit": (768, 1344),
}
# Le nombre de frames suit la grille 17k + 5 du modèle (pas de 17,
# minimum 5, relevés sur le nœud réel).
FRAMES_STILL = 5          # le minimum du nœud, environ 0,2 s
FRAMES_ORBIT = 124        # 17 × 7 + 5, environ cinq secondes à 24 i/s


@dataclass
class Still:
    path: Path
    seed: int
    backend: str
    meta: dict


def new_seed() -> int:
    return random.randrange(1, 2**31 - 1)


def _save_meta(path: Path, meta: dict) -> None:
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _stub_image(kind: str, size: tuple[int, int], seed: int, extra: dict):
    if kind == "face":
        return stubs.portrait(size, seed=seed)
    if kind == "sheet":
        return stubs.plate(size, seed=seed, mask_face=extra.get("mask_face", False))
    return stubs.mannequin(size, azimuth=extra.get("azimuth", 0.0), seed=seed)


def still(kind: str, *, sections: dict, refs: list[Path], dest: Path, seed: int,
          report=lambda p, m: None, extra: dict | None = None) -> Still:
    """Une image par la ruse des cinq frames. `dest` est le PNG final."""
    extra = extra or {}
    size = SIZES[kind]
    backend = config.backend("h3")
    text = prompts.to_text(sections)
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = {"kind": kind, "backend": backend, "seed": seed, "size": list(size), "frames": FRAMES_STILL,
            "refs": [str(r) for r in refs], "prompt": sections}

    if backend == "stub":
        report(0.5, f"factice · {kind}")
        # Le visage et la silhouette du factice suivent la graine du
        # personnage, pas celle de l'essai : sinon les vues d'un même
        # costume montreraient quatre personnes différentes.
        img = _stub_image(kind, size, extra.get("identity_seed", seed), extra)
        img.save(dest)
    else:
        frames = _frames(kind, text, refs, size, seed, FRAMES_STILL, dest.parent / f".{dest.stem}", report)
        best = imaging.sharpest(frames)
        frames[best].convert("RGB").save(dest)
        meta.update(frame_kept=best, frames_returned=len(frames))

    _save_meta(dest, meta)
    report(1.0, f"{dest.name}")
    return Still(dest, seed, backend, meta)


def orbit(*, sections: dict, refs: list[Path], dest_dir: Path, seed: int, azimuths: dict[str, float],
          report=lambda p, m: None, identity_seed: int | None = None) -> dict[str, Still]:
    """L'alternative du §6.1 : un plan en orbite, redécoupé aux azimuts
    voulus.

    La caméra de H3 ne tourne pas à vitesse constante : quand le
    détourage passe par ComfyUI, BiRefNet détoure chaque frame dans le
    même workflow et les frames se choisissent sur la largeur de la
    silhouette (`imaging.orbit_picks`). Sinon, l'azimut d'une frame est
    supposé proportionnel à son rang, et le manifeste le dit."""
    backend = config.backend("h3")
    size = SIZES["orbit"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    if backend == "stub":
        for name, az in azimuths.items():
            img = stubs.mannequin(size, azimuth=az, seed=identity_seed or seed)
            path = dest_dir / f"{name}.png"
            img.save(path)
            meta = {"kind": "orbit", "backend": backend, "seed": seed, "azimuth_assumed": az}
            _save_meta(path, meta)
            out[name] = Still(path, seed, backend, meta)
        return out

    with_masks = backend == "comfyui" and config.backend("prep") == "comfyui"
    frames, masks = _frames("orbit", prompts.to_text(sections), refs, size, seed, FRAMES_ORBIT, dest_dir / ".orbit",
                            report, masks=with_masks)
    n = len(frames)
    if masks:
        widths = [imaging.silhouette_width(m) for m in masks]
        picks = imaging.orbit_picks(widths, azimuths)
        (dest_dir / "orbit.json").write_text(json.dumps({"widths": widths, "picks": picks}, ensure_ascii=False,
                                                        indent=1), encoding="utf-8")
    else:
        picks = {name: {"frame": round(az / 360.0 * n) % n, "azimuth": None, "precision_deg": None,
                        "source": "supposé (vitesse constante)"} for name, az in azimuths.items()}
    for name, az in azimuths.items():
        pick = picks[name]
        i = pick["frame"]
        path = dest_dir / f"{name}.png"
        frames[i].convert("RGB").save(path)
        meta = {"kind": "orbit", "backend": backend, "seed": seed, "frame": i, "frames": n,
                "azimuth_assumed": az, "azimuth_estimated": pick["azimuth"], "azimuth_source": pick["source"],
                "precision_deg": pick["precision_deg"], "prompt": sections}
        _save_meta(path, meta)
        out[name] = Still(path, seed, backend, meta)
    return out


def _frames(kind: str, text: str, refs: list[Path], size: tuple[int, int], seed: int, frames: int,
            workdir: Path, report, masks: bool = False):
    """Les frames rendues ; avec `masks`, rend aussi leurs masques
    BiRefNet, calculés dans le même workflow : (frames, masques)."""
    backend = config.backend("h3")
    if backend == "comfyui":
        images, mask_images = _frames_comfyui(kind, text, refs, size, seed, frames, workdir, report, masks)
    elif backend == "python":
        from . import h3_python

        images = h3_python.frames(text=text, refs=refs, size=size, seed=seed, frames=frames, report=report)
        mask_images = []
    else:
        raise ValueError(f"moteur H3 inconnu : {backend}")
    return (images, mask_images) if masks else images


def _frames_comfyui(kind, text, refs, size, seed, frames, workdir, report, masks: bool = False):
    from .comfy import Comfy, fill, load_template

    comfy = Comfy(config.comfyui_url("h3"))
    template = load_template("h3_orbit.json" if kind == "orbit" else "h3_ref2va.json")
    names = [comfy.upload(Path(r)) for r in refs]
    wf = fill(template, {"prompt": text, "seed": seed, "width": size[0], "height": size[1], "frames": frames},
              names)
    prefixes = {"OUT": kind}
    if masks:
        # BiRefNet sur les frames décodées, avant toute recompression.
        src = next(n for n in wf.values() if n.get("_meta", {}).get("title") == "OUT")["inputs"]["images"]
        wf.update({
            "9001": {"class_type": "LoadBackgroundRemovalModel",
                     "inputs": {"bg_removal_name": config.setting("birefnet", "birefnet.safetensors")}},
            "9002": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["9001", 0], "image": src}},
            "9003": {"class_type": "MaskToImage", "inputs": {"mask": ["9002", 0]}},
            "9004": {"class_type": "SaveImage", "_meta": {"title": "OUT MASK"},
                     "inputs": {"images": ["9003", 0], "filename_prefix": "usine/mask"}},
        })
        prefixes["OUT MASK"] = f"{kind}_mask"
    got = comfy.run_titled(wf, workdir, report=report, prefixes=prefixes)
    images = []
    for p in got["OUT"]:
        # Une sortie animée ou vidéo se déplie en frames.
        images.extend(imaging.frames_of(p))
    if not images:
        raise RuntimeError("H3 n'a rendu aucune frame")
    mask_images = [imaging.load(p).convert("L") for p in got.get("OUT MASK", [])]
    if masks and len(mask_images) != len(images):
        raise RuntimeError(f"{len(images)} frames mais {len(mask_images)} masques")
    return images, mask_images
