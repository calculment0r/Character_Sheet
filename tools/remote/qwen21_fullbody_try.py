"""Plein pied HD par Qwen-Image 2.1 turbo INT8, visage verrouillé d'Essai atelier en <image1>."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

root = Path.home() / "Character_Factory/projects/essai-atelier"
data = json.loads((root / "project.json").read_text())
outfit = data["costumes"]["veryday"]["prompt"]
PROMPT = (
    "Full-body studio photograph of the man in <image1>: the same person, same face, skin tone, hair and age. "
    "He stands in a relaxed A-pose facing the camera straight on: arms held about forty-five degrees away from the "
    "torso, palms turned toward the thighs, fingers relaxed and naturally separated, feet at hip width. "
    f"He wears: {outfit} "
    "The whole figure from the top of the head to the soles of the shoes is inside the frame with an even margin. "
    "Plain uniform light grey seamless studio background, soft even front light. Photorealistic, true-to-life fabric "
    "textures and seams, natural hands with five fingers, sharp focus, high detail.")
print(PROMPT, flush=True)
print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)
out = Path("/tmp/qwen21_fb")
for seed in (21, 22, 23):
    t0 = time.time()
    qwen21.generate(prompt=PROMPT, refs=[root / "face/locked.png"], dest=out / f"fb_{seed}.png", seed=seed,
                    size=qwen21.FULLBODY)
    print(f"graine {seed} : {time.time() - t0:.0f} s", flush=True)
