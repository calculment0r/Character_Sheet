"""Krea 2 : les images du personnage, en photographie.

Décision de Cal (28/09) : Qwen-Image 2.1 n'est pas bon en photographie
— peau plastique, rendu d'image de synthèse — et chaque défaut du
visage ou du plein pied se retrouve dans les planches qui nourrissent la
vidéo. Krea 2 le remplace pour les images qu'on montre et qu'on valide.

Le modèle, sur DGX2 (copié depuis DGX1, où Cal l'avait installé) :
Krea 2 Turbo de Comfy-Org (`krea2_turbo_bf16`, 8 pas, CFG 1, euler,
simple), l'encodeur Qwen3-VL 4B (`CLIPLoader`, type `krea2`) et le VAE
de Qwen-Image. Deux façons de s'en servir :

  sans référence   texte → image ; le négatif est un conditionnement nul
                   (le turbo tourne sans CFG) ;
  une ou deux      l'édition par identité : le LoRA Krea 2 Identity Edit
  références       v1.1 (conradlocke) et les nœuds comfyui-krea2edit. La
                   source entre deux fois, en latent propre devant l'image
                   (`Krea2EditModelPatch`) et vue par l'encodeur avec la
                   consigne (`Krea2EditGroundedEncode`), comme à
                   l'entraînement ; le négatif est la même lecture avec une
                   consigne vide. Deux références : la scène d'abord (elle
                   donne cadre et pose), le sujet ensuite.

Les nœuds `comfyui-krea2edit` (v1.1) ne se branchent plus sur le modèle
dans ComfyUI 0.37, qui gère lui-même les références de Krea 2 (#14843) :
leur encodeur ancré (`Krea2EditGroundedEncode`) sert toujours, les
sources passent en `ReferenceLatent`, méthode `index` (trames 1 et 2).

Banc du 28/09 sur le visage : l'ordonnanceur « beta » donne la peau la
plus fine ; le LoRA de réalisme (RudySen, `krea2_realism`) lisse à 1,6 et
n'apporte pas grand-chose à 1,0 — laissé à 0 par défaut.

Règles du LoRA (README des nœuds) : la sortie garde le rapport de la
source, sinon l'identité se dégrade — chaque référence est donc recadrée
au centre au rapport de la sortie avant l'envoi ; pas plus de 2 Mpx en
édition ; `grounding_px` de 384 à 768 pour la v1.1 (plus haut : plus de
ressemblance, mais des compositions dédoublées).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from . import config
from .comfy import Comfy, fill

UNET = "krea2_turbo_bf16.safetensors"
CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
EDIT_LORA = "krea2_identity_edit_v1_1.safetensors"
REALISM_LORA = "Krea2-realism-V2.safetensors"
STEPS = 8
MAX_REFS = 2
GROUNDING = 768

FACE = (1024, 1024)
FULLBODY = (1152, 2048)


def workflow(n_refs: int, width: int, height: int, *, grounding: int = GROUNDING, realism: float = 0.0,
             edit_strength: float = 1.0, steps: int = STEPS, scheduler: str = "simple") -> dict:
    """Le graphe au format API. `realism` : force du LoRA de réalisme
    (0 : absent)."""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": config.setting("krea2_unet", UNET),
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": config.setting("krea2_clip", CLIP),
                                                     "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": config.setting("krea2_vae", VAE)}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
    }
    model = ["1", 0]
    if realism:
        wf["4"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": model, "lora_name": config.setting("krea2_realism_lora", REALISM_LORA),
                              "strength_model": realism}}
        model = ["4", 0]
    if n_refs:
        wf["5"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": model, "lora_name": config.setting("krea2_edit_lora", EDIT_LORA),
                              "strength_model": edit_strength}}
        model = ["5", 0]
        images = {}
        for k in range(n_refs):
            nid = str(100 + 10 * k)
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}, "_meta": {"title": f"REF {k + 1}"}}
            wf[str(100 + 10 * k + 1)] = {"class_type": "VAEEncode", "inputs": {"pixels": [nid, 0], "vae": ["3", 0]}}
            images["image" if k == 0 else "image_b"] = [nid, 0]
        # Les sources en latents propres, trames 1 et 2 (méthode `index`) :
        # ce que faisait `Krea2EditModelPatch`, que ComfyUI 0.37 fait lui-même
        # (#14843) et qui ne s'y branche plus.
        for cond, prompt in (("8", "{{prompt}}"), ("9", "")):
            wf[cond] = {"class_type": "Krea2EditGroundedEncode",
                        "inputs": {"clip": ["2", 0], "prompt": prompt, "grounding_px": grounding, **images}}
            last = [cond, 0]
            for k in range(n_refs):
                nid = f"{cond}{k}"
                wf[nid] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": last, "latent": [str(100 + 10 * k + 1), 0]}}
                last = [nid, 0]
            wf[f"{cond}m"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                              "inputs": {"conditioning": last, "reference_latents_method": "index"}}
        positive, negative = ["8m", 0], ["9m", 0]
    else:
        wf["8"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "{{prompt}}"}}
        wf["9"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["8", 0]}}
        positive, negative = ["8", 0], ["9", 0]
    wf.update({
        "10": {"class_type": "KSampler",
               "inputs": {"model": model, "positive": positive, "negative": negative, "latent_image": ["7", 0],
                          "seed": "{{seed}}", "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                          "scheduler": scheduler, "denoise": 1.0}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": "usine/krea2"},
               "_meta": {"title": "OUT"}},
    })
    return wf


FACE_PASS = ("Replace only the face of the person in the first image with the face of the person in the second "
             "image. There is only one person in the picture. Keep the head angle, the expression, anything worn on "
             "the head or face, the hair, the clothing, the framing, the light and the background exactly as they "
             "are. Photograph with natural skin texture.")
FACE_PASS_SIDE = 768
FACE_PASS_GROUNDING = 512


def face_pass(src: Path, face: Path, dest: Path, *, box, seed: int, prompt: str = FACE_PASS,
              side: int = FACE_PASS_SIDE, report=lambda p, m: None, grounding: int = FACE_PASS_GROUNDING) -> Path:
    """Le visage d'une image en pied, repris en gros d'après le visage
    verrouillé : dans le plein pied, la tête ne fait que 200 px, trop peu
    pour que l'édition y porte l'identité. La tête (boîte `box` du visage,
    élargie aux cheveux) est recadrée au carré, éditée à `side` px avec le
    visage verrouillé en sujet, puis recollée avec un bord fondu — le
    reste de l'image ne bouge pas.

    Essais du 28/09 (seed, FaceNet contre le visage verrouillé) : tête
    seule 0,62 ; deux références et « une seule personne », ancrage 512 :
    0,79 ; ancrage 768 ou 1024 : la personne dédoublée côte à côte ; tout
    le plein pied en deux références : 0,67 en 150 s."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.open(src).convert("RGB")
    x0, y0, x1, y1 = box
    span = max(x1 - x0, y1 - y0) * 2.4
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 0.1 * span
    n = int(min(span, img.width, img.height))
    left = int(min(max(cx - n / 2, 0), img.width - n))
    top = int(min(max(cy - n / 2, 0), img.height - n))
    work = dest.parent / f".{dest.stem}"
    work.mkdir(parents=True, exist_ok=True)
    crop = work / "head.png"
    img.crop((left, top, left + n, top + n)).resize((side, side), Image.LANCZOS).save(crop)
    edited = generate(prompt=prompt, refs=[crop, face], dest=work / f"head_krea2_{seed}.png", seed=seed,
                      size=(side, side), report=report, grounding=grounding)
    head = Image.open(edited).convert("RGB").resize((n, n), Image.LANCZOS)
    mask = Image.new("L", (n, n), 0)
    pad = int(n * 0.12)
    ImageDraw.Draw(mask).ellipse((pad, pad, n - pad, n - pad), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(n * 0.06))
    img.paste(head, (left, top), mask)
    img.save(dest)
    return dest


def fit(src: Path, size: tuple[int, int], dest: Path) -> Path:
    """La référence recadrée au centre au rapport de la sortie, puis mise
    à sa taille : la source d'une édition a toujours le cadre de l'image
    qu'on attend."""
    from PIL import Image, ImageOps

    img = Image.open(src).convert("RGB")
    ImageOps.fit(img, size, method=Image.LANCZOS, centering=(0.5, 0.5)).save(dest)
    return dest


def generate(*, prompt: str, refs: list[Path] | None = None, dest: Path, seed: int, size: tuple[int, int],
             report=lambda p, m: None, stub=None, grounding: int | None = None, realism: float | None = None,
             edit_strength: float = 1.0, steps: int = STEPS, scheduler: str | None = None) -> Path:
    """Rend une image dans `dest`. `refs` : aucune (texte → image), une
    (la source à éditer) ou deux (la scène, puis le sujet). `stub` : l'image
    à écrire en factice."""
    refs = list(refs or [])[:MAX_REFS]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if config.backend("portrait") == "stub":
        report(0.5, "factice · Krea 2")
        stub().save(dest)
        return dest
    if realism is None:
        realism = float(config.setting("krea2_realism", "0") or 0)
    # « beta » : la peau la plus fine au banc du 28/09 (texte → image) ; les
    # éditions restent en « simple », le réglage où elles ont été essayées.
    scheduler = scheduler or config.setting("krea2_scheduler" if not refs else "krea2_edit_scheduler",
                                            "beta" if not refs else "simple")
    work = dest.parent / f".{dest.stem}"
    work.mkdir(parents=True, exist_ok=True)
    comfy = Comfy(config.comfyui_url("portrait"))
    names = []
    for k, r in enumerate(refs):
        tag = uuid.uuid4().hex[:8]
        names.append(comfy.upload(fit(Path(r), size, work / f"krea2_ref{k + 1}_{tag}.png")))
    wf = fill(workflow(len(names), *size, grounding=grounding or GROUNDING, realism=realism,
                       edit_strength=edit_strength, steps=steps, scheduler=scheduler),
              {"prompt": prompt, "seed": seed}, names)
    files = comfy.run(wf, work, report=report, prefix="krea2")
    Path(files[0]).replace(dest)
    return dest
