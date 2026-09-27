"""Vues d'Essai atelier, une édition par vue guidée par le squelette A-pose
tourné à l'azimut de la vue (apose_skeleton.py --vues=…) : l'A-pose de
face en <image1>, le squelette de la vue en <image2>."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

front = Path("/tmp/apose/apose_fb_32.png")
out = Path("/tmp/views")
# azimut : 0 face, 90 flanc gauche du personnage (+X), 180 dos, 270 flanc droit
VIEWS = {
    45: "a three-quarter front view: he is turned forty-five degrees to his right, so we see his front and his left side",
    90: "an exact left profile: he is turned ninety degrees to his right and faces the left edge of the image; we see "
        "only his left side, his left arm and left leg nearest to the camera",
    180: "a back view: the camera is directly behind him; we see his back, the back of his head and his hood",
    270: "an exact right profile: he is turned ninety degrees to his left and faces the right edge of the image; we see "
         "only his right side, his right arm and right leg nearest to the camera",
    315: "a three-quarter front view: he is turned forty-five degrees to his left, so we see his front and his right side",
}

print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)
for az, text in VIEWS.items():
    prompt = (
        "Full-body studio photograph of the same man as in <image1>, with exactly the same face, hair, skin tone, body "
        "build and the exact same outfit, every garment, colour and detail unchanged, in the same neutral A-pose: arms "
        "straight and held about forty-five degrees out to the sides of the body, legs straight and slightly apart. "
        f"The camera has moved around him: this is {text}. The skeleton in <image2> shows the pose and the viewing "
        "angle; the photograph shows the man alone, without lines or dots. The whole figure from the top of the head "
        "to the soles of the shoes is inside the frame, at the same size and on the same ground line as in <image1>. "
        "Plain uniform light grey seamless studio background, soft even light, eye-level camera, photorealistic, "
        "sharp focus, natural hands with five fingers.")
    t0 = time.time()
    qwen21.generate(prompt=prompt, refs=[front, Path(f"/tmp/apose/skel_{az:03d}.png")], dest=out / f"skel_{az:03d}.png",
                    seed=51, size=(1344, 1792), resolution=1056)
    print(f"vue {az} : {time.time() - t0:.0f} s", flush=True)
