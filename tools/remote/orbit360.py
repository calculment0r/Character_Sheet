"""Orbite 360° d'un personnage : H3 FL2VA pruned int8 + LoRA d'orbite de
pablodawson (`minimax_h3_flf2v_orbit360_v1.safetensors`, HF
pablodawson/MiniMax-H3-360-Orbit-LoRA), la même image en première et
dernière image, 768², 73 images, sans CFG ; nœuds natifs ComfyUI (gabarit
officiel video_minimax_h3_i2v). Essai du 28/09 sur mj-costaud : un tour
complet qui revient sur l'image de départ ; 28 pas en 18 min, turbo 8 pas
(LoRA fl2v turbo 8 steps) en 6 min, de même qualité.

À lancer sur la machine du ComfyUI H3 (:8189), avec un python qui a PIL
et numpy (sur DGX1 : ~/ComfyUI/venv/bin/python) :
  python orbit360.py <image> <nom> [pas] [turbo]
Écrit ~/cf_orbit/<nom>_00001_.mp4. Pas encore un étage de la chaîne."""
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

URL = "http://127.0.0.1:8189"
PROMPT = ("One frozen instant. Only the camera moves. In a continuous 360 orbit. Preserve every person and object in "
          "exactly the same world position, orientation, shape and pose throughout the shot. Airborne objects remain "
          "suspended at the captured height and angle: no wobbling, shaking, spinning, drifting, falling or continued "
          "action. Keep faces, hands, clothing, liquids and the background motionless while retaining their natural "
          "appearance. Camera parallax is the only source of apparent movement. No cuts, zoom, morphing or added "
          "objects.")


def upload(path: Path) -> str:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + (
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(URL + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(urllib.request.urlopen(req).read())["name"]


def square(src: Path, side: int = 768) -> Path:
    """Le personnage au carré sur son propre fond : la hauteur du cadre à
    88 %, centré, le fond prolongé par la couleur des bords."""
    from PIL import Image
    import numpy as np

    img = Image.open(src).convert("RGB")
    a = np.asarray(img)
    bg = tuple(int(x) for x in np.median(np.concatenate([a[:, :10].reshape(-1, 3), a[:, -10:].reshape(-1, 3)]), 0))
    scale = side * 0.92 / img.height
    small = img.resize((max(1, int(img.width * scale)), int(img.height * scale)), Image.LANCZOS)
    out = Image.new("RGB", (side, side), bg)
    out.paste(small, ((side - small.width) // 2, (side - small.height) // 2))
    dest = Path("/tmp") / f"orbit_src_{src.stem}_{side}.png"
    out.save(dest)
    return dest


def main():
    src, name = Path(sys.argv[1]), sys.argv[2]
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 28
    turbo = len(sys.argv) > 4 and sys.argv[4] == "turbo"
    first = upload(square(src))
    model = ["6", 0]
    wf = {
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                                                     "weight_dtype": "default"}},
        "121": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": model, "strength_model": 1.0,
                                                                "lora_name": "minimax_h3_flf2v_orbit360_v1.safetensors"}},
    }
    model = ["121", 0]
    if turbo:
        wf["122"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "strength_model": 1.0,
            "lora_name": "Minimax_H3/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"}}
        model = ["122", 0]
    wf.update({
        "13": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                                                      "type": "minimax", "device": "default"}},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": first}},
        "104": {"class_type": "MiniMaxH3ImageToVideo",
                "inputs": {"clip": ["13", 0], "vae": ["11", 0], "prompt": PROMPT, "width": 768, "height": 768,
                           "length": 73, "first_frame": ["4", 0], "last_frame": ["4", 0]}},
        "16": {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["104", 0]}},
        "17": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": steps,
                                                         "denoise": 1.0}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": 7}},
        "14": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["15", 0], "guider": ["16", 0], "sampler": ["17", 0], "sigmas": ["9", 0],
                          "latent_image": ["104", 1]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["11", 0]}},
        "91": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 24}},
        "92": {"class_type": "SaveVideo", "inputs": {"video": ["91", 0], "filename_prefix": f"orbit/{name}",
                                                     "format": "mp4", "codec": "h264"}},
    })
    req = urllib.request.Request(URL + "/prompt", data=json.dumps({"prompt": wf}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        pid = json.loads(urllib.request.urlopen(req).read())["prompt_id"]
    except urllib.error.HTTPError as exc:
        sys.exit(exc.read().decode()[:2000])
    t0 = time.time()
    while True:
        time.sleep(5)
        hist = json.loads(urllib.request.urlopen(f"{URL}/history/{pid}").read())
        if pid in hist:
            h = hist[pid]
            status = h.get("status", {})
            if status.get("status_str") == "error":
                sys.exit(json.dumps(status)[:2000])
            for out in h["outputs"].values():
                for kind in ("videos", "gifs", "images"):
                    for f in out.get(kind, []):
                        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f["subfolder"],
                                                    "type": f["type"]})
                        dest = Path.home() / "cf_orbit" / f["filename"]
                        dest.parent.mkdir(exist_ok=True)
                        dest.write_bytes(urllib.request.urlopen(f"{URL}/view?{q}").read())
                        print("écrit", dest, round(time.time() - t0), "s", flush=True)
            return


if __name__ == "__main__":
    main()
