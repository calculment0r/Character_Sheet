"""Les vues par édition Qwen : le plein pied de face validé, tourné par un
LoRA d'angle de caméra (§6.1).

H3 ne tient pas les angles d'une génération par vue (il revient vers la
face) et son orbite tourne à vitesse inégale. Trois LoRA existent pour
exactement ce geste — tourner la caméra autour d'un sujet à partir d'une
seule image. Tous trois ont été entraînés sur des objets, aucun sur des
personnages : c'est au banc de dire lequel tient un corps en pied.

  qwen21-orbit  Qwen-Image 2.1 + ML-Intern-lab/Qwen-Image-2.1-viewpoint-orbit-LoRA :
                image détourée (RGBA) en entrée et en sortie, azimut relatif
                à la source (45, 90, 135, 180°), 40 pas, CFG 1. Licence de
                recherche Qwen, non commerciale.
  qwen-2511     Qwen-Image-Edit 2511 + fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA
                (+ Lightning 4 pas) : vue absolue (« left side view »…),
                8 azimuts, 4 hauteurs, 3 distances. Apache 2.0.
  qwen-2509     Qwen-Image-Edit 2509 + dx8152/Qwen-Edit-2509-Multiple-angles
                (+ Lightning 4 pas) : commande relative, en chinois. Apache 2.0.

Les workflows sont construits ici, sur les nœuds natifs de ComfyUI 0.35,
et `./usine doctor` les valide à blanc sur la machine qui les sert
(`comfyui_url_views`).

Le sens des rotations est une convention de chaque LoRA, pas écrite
dans leurs fiches. Celle de la chaîne : la vue `left` montre le côté
gauche du sujet, qui regarde vers la gauche de l'image (la poche de
poitrine de la veste de maren-ostrova, à droite de l'image de face, y
est visible). On suppose « tourner la caméra vers la droite » = aller
vers la gauche du sujet ; à confirmer au premier banc — tout est dans
`TURN` et `ABSOLUTE`, un seul endroit à inverser.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .comfy import Comfy

METHODS = ("qwen21-orbit", "qwen-2511", "qwen-2509")

# Vue de la chaîne → (degrés, sens de la caméra vu depuis l'image de face).
TURN = {"left": (90, "right"), "back": (180, "right"), "right": (90, "left"), "threequarter": (45, "right")}
# Vue de la chaîne → azimut nommé du LoRA de fal (repère de l'objet).
ABSOLUTE = {"front": "front view", "left": "left side view", "back": "back view", "right": "right side view",
            "threequarter": "front-left quarter view"}
CHINESE_SIDE = {"left": "左", "right": "右"}


def prompt(method: str, view: str) -> str:
    if method == "qwen21-orbit":
        deg, side = TURN[view]
        return (f"<orbit> rotate the camera {deg} degrees to the {side}, eye level. "
                "The image has alpha channel and the background is transparent.")
    if method == "qwen-2511":
        return f"<sks> {ABSOLUTE[view]} eye-level shot medium shot"
    if method == "qwen-2509":
        deg, side = TURN[view]
        return f"将镜头向{CHINESE_SIDE[side]}旋转{deg}度"
    raise ValueError(f"méthode de vues inconnue : {method}")


def _edit_plus(unet: str, loras: list[str], shift: float, reference_method: str | None) -> dict:
    """Qwen-Image-Edit (2509, 2511) : le montage de ComfyUI et de fal."""
    wf = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}}}
    last = "1"
    for k, lora in enumerate(loras):
        nid = str(2 + k)
        wf[nid] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": [last, 0], "lora_name": lora, "strength_model": 1.0}}
        last = nid
    wf.update({
        "10": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": [last, 0], "shift": shift}},
        "11": {"class_type": "CFGNorm", "inputs": {"model": ["10", 0], "strength": 1.0}},
        "12": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                                      "type": "qwen_image", "device": "default"}},
        "13": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "14": {"class_type": "LoadImage", "inputs": {"image": "fullbody.png"}, "_meta": {"title": "REF 1"}},
        "15": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["14", 0]}},
        "16": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["12", 0], "vae": ["13", 0], "image1": ["15", 0], "prompt": "{{prompt}}"}},
        "17": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["12", 0], "vae": ["13", 0], "image1": ["15", 0], "prompt": ""}},
        "18": {"class_type": "VAEEncode", "inputs": {"pixels": ["15", 0], "vae": ["13", 0]}},
    })
    pos, neg = ["16", 0], ["17", 0]
    if reference_method:
        wf["19"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                    "inputs": {"conditioning": pos, "reference_latents_method": reference_method}}
        wf["20"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                    "inputs": {"conditioning": neg, "reference_latents_method": reference_method}}
        pos, neg = ["19", 0], ["20", 0]
    wf.update({
        "21": {"class_type": "KSampler",
               "inputs": {"model": ["11", 0], "positive": pos, "negative": neg, "latent_image": ["18", 0],
                          "seed": "{{seed}}", "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                          "scheduler": "simple", "denoise": 1.0}},
        "22": {"class_type": "VAEDecode", "inputs": {"samples": ["21", 0], "vae": ["13", 0]}},
        "23": {"class_type": "SaveImage", "inputs": {"images": ["22", 0], "filename_prefix": "usine/view"},
               "_meta": {"title": "OUT"}},
    })
    return wf


def _qwen21_orbit() -> dict:
    """Qwen-Image 2.1, RGBA de bout en bout : l'image détourée entre avec
    son alpha (JoinImageWithAlpha reprend le masque de LoadImage), le VAE
    de Qwen 2.1 rend du RGBA. 768 px, la résolution d'entraînement du LoRA."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_2.1_bf16.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "strength_model": 1.0,
                                                              "lora_name": "qwen21_viewpoint_orbit_lora.safetensors"}},
        "3": {"class_type": "QwenImage21Cache", "inputs": {"model": ["2", 0], "device": "auto", "dtype": "default"}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image",
                                                     "device": "default"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "6": {"class_type": "LoadImage", "inputs": {"image": "fullbody.png"}, "_meta": {"title": "REF 1"}},
        "7": {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["6", 0], "alpha": ["6", 1]}},
        "8": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["4", 0], "vae": ["5", 0], "prompt": "{{prompt}}", "negative_prompt": "",
                         "resolution": 768, "images.image_1": ["7", 0]}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["3", 0], "positive": ["8", 0], "negative": ["8", 1], "latent_image": ["8", 2],
                         "seed": "{{seed}}", "steps": 40, "cfg": 1.0, "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["5", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "usine/view"},
               "_meta": {"title": "OUT"}},
    }


def workflow(method: str) -> dict:
    if method == "qwen21-orbit":
        return _qwen21_orbit()
    if method == "qwen-2511":
        return _edit_plus("qwen_image_edit_2511_fp8mixed.safetensors",
                          ["qwen-image-edit-2511-multiple-angles-lora.safetensors",
                           "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"], 3.1, "index_timestep_zero")
    if method == "qwen-2509":
        return _edit_plus("qwen_image_edit_2509_fp8_e4m3fn.safetensors",
                          ["Qwen-Edit-2509-Multiple-angles.safetensors",
                           "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors"], 3.0, None)
    raise ValueError(f"méthode de vues inconnue : {method} (possibles : {', '.join(METHODS)})")


def generate(method: str, view: str, *, source: Path, dest: Path, seed: int, report=lambda p, m: None) -> dict:
    """Une vue, tirée de `source` (le plein pied de face ; détouré en RGBA
    pour qwen21-orbit). Écrit `dest` et rend ce qui va au manifeste."""
    from .comfy import fill

    comfy = Comfy(config.comfyui_url("views"))
    text = prompt(method, view)
    wf = fill(workflow(method), {"prompt": text, "seed": seed}, [comfy.upload(Path(source))])
    work = dest.parent / f".{dest.stem}"
    paths = comfy.run(wf, work, report=report, prefix=f"{method}_{view}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    Path(paths[0]).replace(dest)
    return {"method": method, "prompt": text, "seed": seed, "machine": comfy.url}
