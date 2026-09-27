"""Vues d'Essai atelier depuis son A-pose (qwen21_apose_try.py), deux voies :
  planche  une image à quatre cases égales (face, flanc gauche, dos, flanc
           droit) : la cohérence vient de l'attention partagée ;
  vue      une édition par vue, en HD.
Et l'A-pose refaite avec l'encodeur à 1056 au lieu de 1024 (ComfyUI #16435)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

front = Path("/tmp/apose/apose_fb_32.png")
fullbody = Path.home() / "Character_Factory/projects/essai-atelier/costumes/veryday/fullbody.png"
skeleton = Path("/tmp/apose/skel.png")
out = Path("/tmp/views")
KEEP = ("the same man as in <image1>, with exactly the same face, hair, skin tone, body build and the exact same "
        "outfit, every garment, colour and detail unchanged")
POSE = ("standing in the same neutral A-pose as in <image1>: arms straight, about forty-five degrees away from the "
        "body, legs straight and slightly apart")
LOOK = ("Plain uniform light grey seamless studio background, soft even light, orthographic view at waist height, "
        "photorealistic, sharp focus, natural hands with five fingers.")

SHEET = (f"A character turnaround sheet of ONE AND THE SAME man: {KEEP}, {POSE}. Four equal panels side by side, "
         "left to right: front view facing the camera; his left side in profile, facing the left edge of the image; "
         "back view; his right side in profile, facing the right edge of the image. In every panel the whole figure "
         "from the top of the head to the soles of the shoes, the same height, the feet on the same ground line, "
         "the same distance to the camera. " + LOOK)

VIEWS = {
    "back": "Back view: the camera is directly behind him and we see his back, the back of his head and the hood.",
    "left": ("Left side view in exact profile: we see his left side, his body turned ninety degrees so that he "
             "faces the left edge of the image, his left arm toward the camera."),
    "right": ("Right side view in exact profile: we see his right side, his body turned ninety degrees so that he "
              "faces the right edge of the image, his right arm toward the camera."),
}

print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)


def run(name, **kw):
    t0 = time.time()
    qwen21.generate(dest=out / f"{name}.png", **kw)
    print(f"{name} : {time.time() - t0:.0f} s", flush=True)


for seed in (41, 42):
    run(f"sheet_{seed}", prompt=SHEET, refs=[front], seed=seed, size=(2048, 1152), resolution=1056)
for view, text in VIEWS.items():
    run(f"view_{view}", prompt=f"Full-body studio photograph of {KEEP}, {POSE}. {text} The whole figure from the top "
        f"of the head to the soles of the shoes is inside the frame. {LOOK}",
        refs=[front], seed=41, size=(1344, 1792), resolution=1056)
run("apose_1056", prompt=(
    f"Full-body studio photograph of the same man as in <image1>, wearing exactly the same outfit, every garment, "
    "colour, fold and detail unchanged. He now stands in the pose of the skeleton in <image2>: a neutral A-pose, "
    "standing straight and facing the camera, arms straight and held about forty-five degrees away from the body, "
    "hands relaxed with the fingers pointing down and slightly apart, legs straight and slightly apart. The skeleton "
    "only guides the pose; the photograph shows the man alone, without lines or dots. The whole figure from the top "
    "of the head to the soles of the shoes is inside the frame. " + LOOK),
    refs=[fullbody, skeleton], seed=32, size=(1344, 1792), resolution=1056)
