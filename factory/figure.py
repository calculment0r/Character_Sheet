"""Le plein pied par un modèle d'image, le visage verrouillé en référence.

H3 rendait le plein pied comme le visage : un modèle vidéo en turbo, à
768 px de large — le visage y fait soixante pixels, les mains trente,
d'où les doigts faux, les matières cireuses et l'air d'une vignette
agrandie (Cal, 25/09). Deux modèles d'image de DGX2 savent reprendre une
personne d'après une image de référence :

  flux2      FLUX.2 dev, références natives (`ReferenceLatent`) : le
             visage, puis les images de vêtements ;
  qwen2511   Qwen-Image-Edit 2511 (LoRA Lightning 4 pas) : le visage et
             jusqu'à deux images de vêtements.

L'image fait 896 × 1600 px (1152 × 2048 pour Qwen-Image 2.1). La pose est
naturelle, face à l'objectif : c'est l'image que Cal valide ; l'A-pose
vient après, imposée par un squelette (`pose.py`). La tenue est le prompt
du costume (écrit par le modèle de texte depuis son brief).
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .comfy import Comfy, fill

ENGINES = {
    "qwen21": "Qwen-Image 2.1 turbo · HD",
    "flux2": "FLUX.2 dev",
    "qwen2511": "Qwen-Image-Edit 2511",
    "h3": "H3 · 768 px",
}
W, H = 896, 1600
MAX_REFS = {"flux2": 6, "qwen2511": 3, "qwen21": 3}


def text(outfit: str, *, garments: int = 0, style: str = "photoreal", tags: bool = False) -> str:
    """`tags` : Qwen-Image 2.1 nomme ses références <image1>, <image2>…"""
    look = ("Photorealistic, true-to-life fabric textures and seams, natural hands with five fingers, sharp focus, "
            "high detail." if style == "photoreal" else
            "Stylised character design reference, clean shapes, consistent shading, true colours, natural hands.")
    person = "the person in <image1>" if tags else "the same person as in the first reference image"
    others = ", ".join(f"<image{k}>" for k in range(2, 2 + garments)) if tags else "the other reference images"
    worn = (f"The outfit is the one shown in {others}, every piece kept as it is. " if garments else "")
    return " ".join(filter(None, [
        f"Full-body studio photograph of {person}: keep the face, skin tone, hair, age and build exactly.",
        "The person stands in a natural, relaxed pose, weight on both feet, arms resting naturally, facing the "
        "camera straight on at eye level.",
        "The whole figure from the top of the head to the soles of the shoes is inside the frame with an even "
        "margin.",
        worn + (f"Outfit: {outfit.strip()}" if outfit.strip() else ""),
        "Plain uniform light grey seamless background, soft even studio light from the front, no text, no logo.",
        look,
    ]))


def _flux2(n_refs: int) -> dict:
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux2_dev_fp8mixed.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "mistral_3_small_flux2_bf16.safetensors",
                                                     "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "{{prompt}}"}},
    }
    cond = ["4", 0]
    for k in range(n_refs):
        b = 100 + 10 * k
        wf[str(b)] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}, "_meta": {"title": f"REF {k + 1}"}}
        wf[str(b + 1)] = {"class_type": "ImageScaleToTotalPixels",
                          "inputs": {"image": [str(b), 0], "upscale_method": "lanczos", "megapixels": 1.0,
                                     "resolution_steps": 1}}
        wf[str(b + 2)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(b + 1), 0], "vae": ["3", 0]}}
        wf[str(b + 3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": cond, "latent": [str(b + 2), 0]}}
        cond = [str(b + 3), 0]
    wf.update({
        "9": {"class_type": "FluxGuidance", "inputs": {"conditioning": cond, "guidance": 4.0}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["9", 0]}},
        "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "12": {"class_type": "Flux2Scheduler", "inputs": {"steps": 28, "width": W, "height": H}},
        "13": {"class_type": "RandomNoise", "inputs": {"noise_seed": "{{seed}}"}},
        "14": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "15": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["13", 0], "guider": ["10", 0], "sampler": ["11", 0], "sigmas": ["12", 0],
                          "latent_image": ["14", 0]}},
        "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["3", 0]}},
        "17": {"class_type": "SaveImage", "inputs": {"images": ["16", 0], "filename_prefix": "usine/fullbody"},
               "_meta": {"title": "OUT"}},
    })
    return wf


def _qwen2511(n_refs: int) -> dict:
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2511_fp8mixed.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "strength_model": 1.0,
                         "lora_name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["2", 0], "shift": 3.1}},
        "4": {"class_type": "CFGNorm", "inputs": {"model": ["3", 0], "strength": 1.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                                     "type": "qwen_image", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
    }
    images = {}
    for k in range(n_refs):
        b = 100 + 10 * k
        wf[str(b)] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}, "_meta": {"title": f"REF {k + 1}"}}
        wf[str(b + 1)] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [str(b), 0]}}
        images[f"image{k + 1}"] = [str(b + 1), 0]
    wf.update({
        "9": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["5", 0], "vae": ["6", 0], "prompt": "{{prompt}}", **images}},
        "10": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["5", 0], "vae": ["6", 0], "prompt": "", **images}},
        "11": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "12": {"class_type": "KSampler",
               "inputs": {"model": ["4", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["11", 0],
                          "seed": "{{seed}}", "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                          "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "usine/fullbody"},
               "_meta": {"title": "OUT"}},
    })
    return wf


def workflow(engine: str, n_refs: int) -> dict:
    if engine == "flux2":
        return _flux2(n_refs)
    if engine == "qwen2511":
        return _qwen2511(n_refs)
    raise ValueError(f"moteur de plein pied inconnu : {engine} (possibles : {', '.join(ENGINES)})")


def generate(engine: str, *, prompt: str, refs: list[Path], dest: Path, seed: int, report=lambda p, m: None,
             identity_seed: int | None = None) -> Path:
    """Rend un plein pied dans `dest`. `refs` : le visage verrouillé
    d'abord, puis les images de vêtements (tronquées au maximum du modèle)."""
    refs = list(refs)[:MAX_REFS.get(engine, 1)]
    if engine == "qwen21":
        from . import qwen21
        from . import stubs as sketches

        return qwen21.generate(prompt=prompt, refs=refs, dest=dest, seed=seed, size=qwen21.FULLBODY, report=report,
                               stub=lambda: sketches.mannequin(qwen21.FULLBODY, azimuth=0.0,
                                                            seed=identity_seed if identity_seed is not None else seed))
    if config.backend("portrait") == "stub":
        from . import stubs

        report(0.5, f"factice · plein pied {engine}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        stubs.mannequin((W, H), azimuth=0.0, seed=identity_seed if identity_seed is not None else seed).save(dest)
        return dest
    comfy = Comfy(config.comfyui_url("portrait"))
    names = [comfy.upload(Path(r)) for r in refs]
    wf = fill(workflow(engine, len(names)), {"prompt": prompt, "seed": seed}, names)
    files = comfy.run(wf, dest.parent / f".{dest.stem}", report=report, prefix="fullbody")
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path(files[0]).replace(dest)
    return dest
