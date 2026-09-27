"""Pixal3D sur une orbite libre : n'importe quelles vues, à n'importe quels
azimuts (l'amont, inference_mv.py, le permet ; le nœud natif de ComfyUI
fige face/gauche/dos/droite). Essayé le 27/09 sur DGX1 avec face + 3/4 +
dos (docs/ETUDES.md §4.1) — PAS branché dans la chaîne.

Pour l'essayer sans toucher un ComfyUI en service : le copier en
<base>/custom_nodes/cf_orbit/__init__.py et lancer une instance à part,
  python main.py --port 8190 --base-directory <base> --models-directory ~/ComfyUI/models
Entrées : azimuths « 0,36.4,180.6 » (0 face, 90 flanc gauche), image_1…n
recadrées par ImageCropToMask comme pour le nœud natif."""
import math

import comfy.utils
import torch
from comfy_extras.nodes_trellis2 import _VIEW_PAD, _build_pixal3d_conditioning, _orbit_camera_to_world


class Pixal3DOrbitConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        opt = {f"image_{i}": ("IMAGE",) for i in range(1, 9)}
        return {"required": {"clip_vision_model": ("CLIP_VISION",),
                             "fov": ("FLOAT", {"default": 20.0, "min": 1.0, "max": 170.0, "step": 0.01}),
                             "azimuths": ("STRING", {"default": "0,90,180,270"})},
                "optional": opt}

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "run"
    CATEGORY = "model/conditioning/trellis"

    def run(self, clip_vision_model, fov, azimuths, **images):
        az = [float(a) for a in azimuths.split(",") if a.strip()]
        views = [images.get(f"image_{i}") for i in range(1, len(az) + 1)]
        if any(v is None for v in views):
            raise ValueError("one image per azimuth")
        az = [a - az[0] for a in az]
        items = []
        for v in views:
            v = v[:1]
            if v.shape[-1] == 4:
                v = v[..., :3] * v[..., 3:4]
            if v.shape[1:3] != (1024, 1024):
                v = comfy.utils.common_upscale(v.movedim(-1, 1), 1024, 1024, "lanczos", "disabled").movedim(1, -1)
            items.append(v)
        f = math.radians(fov)
        c2w = _orbit_camera_to_world(az, [0.0] * len(az), _VIEW_PAD * 0.5 / math.tan(f / 2.0))
        out = _build_pixal3d_conditioning(clip_vision_model, torch.cat(items, 0), c2w,
                                          torch.full((len(az),), f), torch.ones(1), num_views=len(az))
        return tuple(out.args)


NODE_CLASS_MAPPINGS = {"Pixal3DOrbitConditioning": Pixal3DOrbitConditioning}
