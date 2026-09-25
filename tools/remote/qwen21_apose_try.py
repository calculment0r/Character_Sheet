"""Remise en A-pose par Qwen-Image 2.1 turbo : le plein pied validé d'Essai
atelier en <image1>, le squelette A-pose (apose_skeleton.py) en <image2>,
le visage verrouillé en <image3> pour la seconde série."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

root = Path.home() / "Character_Factory/projects/essai-atelier"
data = json.loads((root / "project.json").read_text())
outfit = data["costumes"]["veryday"]["outfit"]["default_outfit_description"]
fullbody = root / "costumes/veryday/fullbody.png"
face = root / "face/locked.png"
skeleton = Path("/tmp/apose/skel.png")
SIZE = (1344, 1792)


def prompt(with_face: bool) -> str:
    who = ("the same man as in <image1>, with the face of <image3>" if with_face else "the same man as in <image1>")
    return (
        f"Full-body studio photograph of {who}: the same face, hair, skin tone, age and body build, wearing exactly "
        f"the same outfit as in <image1> ({outfit}), every garment, colour, fold and detail unchanged. "
        "He now stands in the pose of the skeleton in <image2>: a neutral A-pose, standing straight and facing the "
        "camera, arms straight and held about forty-five degrees away from the body, hands relaxed with the fingers "
        "pointing down and slightly apart, legs straight and slightly apart. The skeleton only guides the pose; the "
        "photograph shows the man alone, without lines or dots. The whole figure from the top of the head to the "
        "soles of the shoes is inside the frame. Plain uniform light grey seamless studio background, soft even "
        "front light. Photorealistic, true-to-life fabric textures, natural hands with five fingers, sharp focus.")


print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)
out = Path("/tmp/apose")
for with_face in (False, True):
    p = prompt(with_face)
    print(p, flush=True)
    refs = [fullbody, skeleton] + ([face] if with_face else [])
    for seed in (31, 32):
        t0 = time.time()
        name = f"apose_{'face' if with_face else 'fb'}_{seed}.png"
        qwen21.generate(prompt=p, refs=refs, dest=out / name, seed=seed, size=SIZE)
        print(f"{name} : {time.time() - t0:.0f} s", flush=True)
