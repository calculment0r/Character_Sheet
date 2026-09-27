"""Le portrait neutre du §3 par un modèle d'image, au lieu de H3.

Le brief laisse le modèle du visage au choix (« FLUX, SDXL, Qwen-Image…
mais il doit tourner en local »). H3 est un modèle vidéo, rendu ici en
turbo quatre pas à 768 px : pour l'image qui fait autorité sur toute
l'identité, un modèle d'image fait mieux. Trois sont déjà sur DGX2, dans
le ComfyUI principal (`:8188`), avec leurs nœuds natifs :

  zimage   Z-Image Turbo (Tongyi), 8 pas, encodeur Qwen3-4B — rapide ;
  flux2    FLUX.2 dev (fp8), encodeur Mistral Small 3 — le plus lourd ;
  qwen21   Qwen-Image 2.1, encodeur Qwen3-VL-8B.

Le prompt ne décrit que l'identité : âge, genre, origine, carrure, et
les précisions du visage. Ni rôle, ni archétype, ni notes : ce sont des
notes de costume, et un « skateur » dont la note parle d'un « casque »
sort casqué, lunettes de ski comprises (Kévin, 25/09). Tête nue, et
en gros plan, coupé au cou : un vêtement sur le portrait passerait dans
toutes les générations qui le prennent en référence (Cal, 25/09).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config
from .comfy import Comfy, fill

ENGINES = {
    "h3": "H3 · Ref2VA, 768 px",
    "zimage": "Z-Image Turbo",
    "flux2": "FLUX.2 dev",
    "qwen21": "Qwen-Image 2.1",
}
SIZE = 1024


def who(sheet: dict) -> str:
    """Âge, genre, origine, carrure : ce qui se voit sur un visage."""
    age = str(sheet.get("age") or "").strip()
    bits = []
    if age:
        bits.append(f"{age}-year-old" if age.isdigit() else age)
    for key, fmt in (("gender", "{}"), ("ethnicity", "{}"), ("body_type", "{} build")):
        if sheet.get(key):
            bits.append(fmt.format(str(sheet[key]).strip()))
    return ", ".join(bits) or "a person"


def text(sheet: dict, extra: str = "", style: str = "photoreal") -> str:
    """Un paragraphe, pour un modèle d'image : pas de sections, pas de
    négatif — les contraintes s'écrivent en prose."""
    look = ("Photorealistic casting headshot, natural skin texture with visible pores, true-to-life colour, "
            "sharp focus, 85 mm portrait lens." if style == "photoreal" else
            "Stylised character portrait for a production design reference, clean shapes, consistent shading, "
            "true colours.")
    return " ".join(filter(None, [
        f"Tight close-up identity portrait of the face of {sheet.get('character_name') or 'the character'}, "
        f"{who(sheet)}.",
        extra.strip().rstrip(".") + "." if extra.strip() else "",
        "The face fills the frame, from just above the top of the hair to just below the chin; the frame is "
        "cropped at the neck, with no shoulders and no clothing visible.",
        "Facing the camera straight on at eye level, centred, neutral relaxed expression, mouth closed, eyes "
        "looking straight into the lens.",
        "The head is bare and the hair fully visible: no hat, no cap, no hood, no helmet, no headphones, no "
        "glasses or goggles, no earrings, no jewellery.",
        "Soft, even, neutral studio light from the front, identical on both sides of the face, plain uniform "
        "light grey seamless background, no text, no logo.",
        look,
    ]))


def workflow(engine: str) -> dict:
    out = {"class_type": "SaveImage", "inputs": {"filename_prefix": "usine/portrait"}, "_meta": {"title": "OUT"}}
    if engine == "zimage":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_bf16.safetensors",
                                                         "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2",
                                                         "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "{{prompt}}"}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": SIZE, "height": SIZE, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                       "latent_image": ["7", 0], "seed": "{{seed}}", "steps": 8,
                                                       "cfg": 1.0, "sampler_name": "res_multistep",
                                                       "scheduler": "simple", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {**out, "inputs": {**out["inputs"], "images": ["9", 0]}},
        }
    if engine == "flux2":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux2_dev_fp8mixed.safetensors",
                                                         "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "mistral_3_small_flux2_bf16.safetensors",
                                                         "type": "flux2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "{{prompt}}"}},
            "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 4.0}},
            "6": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
            "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "8": {"class_type": "Flux2Scheduler", "inputs": {"steps": 28, "width": SIZE, "height": SIZE}},
            "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": "{{seed}}"}},
            "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": SIZE, "height": SIZE, "batch_size": 1}},
            "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["9", 0], "guider": ["6", 0],
                                                                     "sampler": ["7", 0], "sigmas": ["8", 0],
                                                                     "latent_image": ["10", 0]}},
            "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
            "13": {**out, "inputs": {**out["inputs"], "images": ["12", 0]}},
        }
    if engine == "qwen21":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_bf16.safetensors",
                                                         "weight_dtype": "default"}},
            "2": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_bf16.safetensors",
                                                         "type": "qwen_image", "device": "default"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
            "5": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["3", 0], "vae": ["4", 0],
                                                                    "prompt": "{{prompt}}", "negative_prompt": "",
                                                                    "resolution": SIZE}},
            "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1],
                                                       "latent_image": ["5", 2], "seed": "{{seed}}", "steps": 40,
                                                       "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                                                       "denoise": 1.0}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["4", 0]}},
            "8": {**out, "inputs": {**out["inputs"], "images": ["7", 0]}},
        }
    raise ValueError(f"moteur de portrait inconnu : {engine} (possibles : {', '.join(ENGINES)})")


def generate(engine: str, *, prompt: str, dest: Path, seed: int, report=lambda p, m: None,
             identity_seed: int | None = None) -> Path:
    """Rend un portrait dans `dest` (PNG). En factice, le visage suit la
    graine d'identité du personnage, comme pour H3."""
    if config.backend("portrait") == "stub":
        from . import stubs

        report(0.5, f"factice · portrait {engine}")
        # Pour les essais de la page : un factice qui prend son temps, comme un vrai rendu.
        time.sleep(float(config.setting("stub_delay", "0") or 0))
        dest.parent.mkdir(parents=True, exist_ok=True)
        stubs.portrait((SIZE, SIZE), seed=identity_seed if identity_seed is not None else seed).save(dest)
        return dest
    comfy = Comfy(config.comfyui_url("portrait"))
    wf = fill(workflow(engine), {"prompt": prompt, "seed": seed}, [])
    files = comfy.run(wf, dest.parent / f".{dest.stem}", report=report, prefix="portrait")
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path(files[0]).replace(dest)
    return dest
