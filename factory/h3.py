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

# 768 de petit côté, arrondi au multiple de 16 que les VAE vidéo exigent.
SIZES = {
    "face": (768, 768),
    "fullbody": (768, 1360),
    "sheet": (1360, 768),
    "view": (768, 1360),
    "orbit": (768, 1360),
}
FRAMES_STILL = 5          # le minimum du nœud, environ 0,2 s
FRAMES_ORBIT = 121        # environ cinq secondes à 24 i/s


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
    voulus. L'azimut d'une frame est supposé proportionnel à son rang —
    la caméra tourne à vitesse constante — et le contrôle le dit."""
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

    frames = _frames("orbit", prompts.to_text(sections), refs, size, seed, FRAMES_ORBIT, dest_dir / ".orbit", report)
    n = len(frames)
    for name, az in azimuths.items():
        i = round(az / 360.0 * n) % n
        path = dest_dir / f"{name}.png"
        frames[i].convert("RGB").save(path)
        meta = {"kind": "orbit", "backend": backend, "seed": seed, "frame": i, "frames": n,
                "azimuth_assumed": az, "prompt": sections}
        _save_meta(path, meta)
        out[name] = Still(path, seed, backend, meta)
    return out


def _frames(kind: str, text: str, refs: list[Path], size: tuple[int, int], seed: int, frames: int,
            workdir: Path, report) -> list:
    backend = config.backend("h3")
    if backend == "comfyui":
        return _frames_comfyui(kind, text, refs, size, seed, frames, workdir, report)
    if backend == "python":
        from . import h3_python

        return h3_python.frames(text=text, refs=refs, size=size, seed=seed, frames=frames, report=report)
    raise ValueError(f"moteur H3 inconnu : {backend}")


def _frames_comfyui(kind, text, refs, size, seed, frames, workdir, report) -> list:
    from .comfy import Comfy, fill, load_template

    comfy = Comfy()
    template = load_template("h3_orbit.json" if kind == "orbit" else "h3_ref2va.json")
    names = [comfy.upload(Path(r)) for r in refs]
    wf = fill(template, {"prompt": text, "seed": seed, "width": size[0], "height": size[1], "frames": frames},
              names)
    paths = comfy.run(wf, workdir, report=report, prefix=kind)
    images = []
    for p in paths:
        img = imaging.load(p)
        # Une sortie vidéo animée (webp, gif) se déplie en frames.
        n = getattr(img, "n_frames", 1)
        for i in range(n):
            img.seek(i)
            images.append(img.convert("RGB").copy())
    if not images:
        raise RuntimeError("H3 n'a rendu aucune frame")
    return images
