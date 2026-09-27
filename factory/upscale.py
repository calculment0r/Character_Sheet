"""Agrandir une image sans rien inventer de nouveau : les détails de la
planche de présentation sont des recadrages du plein pied, agrandis.

  seedvr2     SeedVR2 natif ComfyUI (poids `Comfy-Org/SeedVR2`, en INT8),
              le montage du gabarit officiel `utility_seedvr2_*_int8_upscale_image` :
              l'image agrandie en Lanczos, puis une passe de restauration
              en un pas, couleurs recalées sur l'entrée (lab) ;
  realesrgan  secours : RealESRGAN ×2 par ImageUpscaleWithModel ;
  lanczos     en factice, ou si ComfyUI ne peut rien.

`upscaler` (FACTORY_UPSCALER) choisit ; `seedvr2_unet` le modèle.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import config
from .comfy import Comfy, ComfyError

SEEDVR2_UNET = "seedvr2_7b_int8_convrot.safetensors"
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"
ESRGAN = "RealESRGAN_x2.pth"


def seedvr2_workflow(name: str, seed: int, unet: str | None = None) -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": name}},
        "2": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["1", 0]}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": config.setting("seedvr2_vae", SEEDVR2_VAE)}},
        "4": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["2", 0], "vae": ["3", 0], "tile_size": 512,
                                                         "overlap": 128, "temporal_size": 4096, "temporal_overlap": 8}},
        "5": {"class_type": "UNETLoader", "inputs": {"unet_name": unet or config.setting("seedvr2_unet", SEEDVR2_UNET),
                                                     "weight_dtype": "default"}},
        "6": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["5", 0], "vae_conditioning": ["4", 0]}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["6", 0], "negative": ["6", 1],
                                                   "latent_image": ["4", 0], "seed": seed, "steps": 1, "cfg": 1.0,
                                                   "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["7", 0], "vae": ["3", 0], "tile_size": 512,
                                                         "overlap": 128, "temporal_size": 4096, "temporal_overlap": 8}},
        "9": {"class_type": "SeedVR2PostProcessing", "inputs": {"images": ["8", 0], "original_resized_images": ["1", 0],
                                                                "color_correction_method": "lab"}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "usine/seedvr2"},
               "_meta": {"title": "OUT"}},
    }


def esrgan_workflow(name: str) -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": config.setting("esrgan", ESRGAN)}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "usine/esrgan"},
              "_meta": {"title": "OUT"}},
    }


def upscale(img: Image.Image, dest: Path, *, factor: int = 4, seed: int = 0, workdir: Path,
            engine: str | None = None) -> str:
    """Agrandit `img` d'un facteur `factor` dans `dest` ; rend le moteur
    qui l'a fait."""
    img = img.convert("RGB")
    size = (img.width * factor, img.height * factor)
    big = img.resize(size, Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    engine = engine or config.setting("upscaler", "seedvr2")
    if config.backend("portrait") == "stub" or engine == "lanczos":
        big.save(dest)
        return "lanczos"
    comfy = Comfy(config.comfyui_url("portrait"))
    workdir.mkdir(parents=True, exist_ok=True)
    for name in ([engine] if engine != "seedvr2" else ["seedvr2", "realesrgan"]):
        try:
            if name == "seedvr2":
                src = workdir / f"{dest.stem}_in.png"
                big.save(src)
                wf = seedvr2_workflow(comfy.upload(src), seed)
            else:
                src = workdir / f"{dest.stem}_in.png"
                img.save(src)
                wf = esrgan_workflow(comfy.upload(src))
            out = comfy.run(wf, workdir, prefix=dest.stem)[0]
            res = Image.open(out).convert("RGB")
            if res.size != size:
                res = res.resize(size, Image.LANCZOS)
            res.save(dest)
            Path(out).unlink()
            src.unlink()
            return name
        except ComfyError as exc:
            print(f"    {name} indisponible ({str(exc)[:160]}) — secours")
    big.save(dest)
    return "lanczos"
