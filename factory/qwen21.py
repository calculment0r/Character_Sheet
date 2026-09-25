"""Qwen-Image 2.1 en turbo : les images de validation, en HD.

Décision de Cal (25/09) : les images qu'on valide — visage, plein pied,
vues — sortent de Qwen-Image 2.1, qui génère et édite ; H3 ne sert plus
qu'à la fin, pour le turnaround de présentation d'un personnage validé.

Le modèle : la base INT8 de Comfy-Org (`qwen_image_2.1_int8_convrot`,
7,3 Go), l'encodeur Qwen3-VL-8B INT8, et le LoRA turbo de Viggle v0.2.1
(distillé DMD, 6 pas au lieu de 40, sans CFG, génération et édition avec
une à trois références), converti pour ComfyUI par t8star. Montage de
t8star, nœuds natifs seulement :

  - le LoRA est appliqué sans fusion (`LoraLoaderBypassModelOnly`, force
    1,0) : fusionné dans les poids, il perd une partie de sa mise à jour ;
  - 6 pas sur les nœuds bruts [1, 0,9375, 0,875, 0,75, 0,5, 0,25], décalés
    selon la résolution (`sigmas`), euler, `BasicGuider`, pas de négatif.

Les références s'appellent <image1>, <image2>… dans le prompt, dans
l'ordre d'envoi. La taille de sortie est libre (`EmptyLatentImage`) : le
plein pied se rend en 1152 × 2048, l'aire d'entraînement de l'édition.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import config
from .comfy import Comfy, fill

UNET = "qwen_image_2.1_int8_convrot.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
LORA = "qwen_image_2.1_viggle_turbo_v0.2.1_r256_comfy.safetensors"
NODES = (0.9375, 0.875, 0.75, 0.5, 0.25)
MAX_REFS = 3

FACE = (1024, 1024)
FULLBODY = (1152, 2048)


def sigmas(width: int, height: int) -> str:
    """Les 6 pas du turbo, décalés comme le fait le pipeline : mu suit le
    nombre de jetons de l'image (un jeton = 16 × 16 px), décalage e^mu."""
    tokens = (height // 16) * (width // 16)
    shift = math.e ** (0.5 + 0.4 * (tokens - 256) / 7936)
    return ", ".join(["1"] + [f"{shift / (shift + 1 / x - 1):.10f}" for x in NODES] + ["0"])


def workflow(n_refs: int, width: int, height: int, resolution: int = 1024) -> dict:
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": config.setting("qwen21_unet", UNET),
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderBypassModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": config.setting("qwen21_lora", LORA), "strength_model": 1.0}},
        "3": {"class_type": "QwenImage21Cache", "inputs": {"model": ["2", 0], "device": "off", "dtype": "default"}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": config.setting("qwen21_clip", CLIP),
                                                     "type": "qwen_image", "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }
    images = {}
    for k in range(n_refs):
        nid = str(100 + k)
        wf[nid] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}, "_meta": {"title": f"REF {k + 1}"}}
        images[f"images.image_{k + 1}"] = [nid, 0]
    wf.update({
        "6": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["4", 0], "vae": ["5", 0], "prompt": "{{prompt}}", "negative_prompt": "",
                         "resolution": resolution, **images}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "ManualSigmas", "inputs": {"sigmas": sigmas(width, height)}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["3", 0], "conditioning": ["6", 0]}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": "{{seed}}"}},
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "12": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["9", 0], "sampler": ["11", 0], "sigmas": ["8", 0],
                          "latent_image": ["7", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "usine/qwen21"},
               "_meta": {"title": "OUT"}},
    })
    return wf


def generate(*, prompt: str, refs: list[Path], dest: Path, seed: int, size: tuple[int, int],
             report=lambda p, m: None, stub=None, resolution: int = 1024) -> Path:
    """Rend une image dans `dest`. `refs` : <image1>, <image2>… (trois au
    plus). `stub` : l'image à écrire en factice. `resolution` : taille à
    laquelle l'encodeur lit les références."""
    refs = list(refs)[:MAX_REFS]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if config.backend("portrait") == "stub":
        report(0.5, "factice · Qwen-Image 2.1")
        stub().save(dest)
        return dest
    comfy = Comfy(config.comfyui_url("portrait"))
    names = [comfy.upload(Path(r)) for r in refs]
    wf = fill(workflow(len(names), *size, resolution=resolution),{"prompt": prompt, "seed": seed}, names)
    files = comfy.run(wf, dest.parent / f".{dest.stem}", report=report, prefix="qwen21")
    Path(files[0]).replace(dest)
    return dest
