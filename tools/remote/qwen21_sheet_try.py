"""Planche de référence façon « Qwen Image 2.1 Character Reference Sheet
Generator » (Civitai 2960890) : trois cases — plein pied de face, plein
pied de dos, gros plan tête et épaules — le visage en <image1>, le
personnage en A-pose (tenue exacte) en <image2>, une mise en page en
<image3> qui ne donne que la composition. Deux mises en page essayées :
les squelettes A-pose seuls, et un mannequin rendu d'après eux."""
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

root = Path.home() / "Character_Factory/projects/essai-atelier"
face = root / "face/locked.png"
apose = Path("/tmp/apose/apose_fb_32.png")
out = Path("/tmp/sheet")
out.mkdir(parents=True, exist_ok=True)
W, H = 1920, 1088
PANEL = W // 3


def content_box(img: Image.Image):
    return img.convert("L").point(lambda v: 255 if v > 20 else 0).getbbox()


def layout() -> Path:
    """Face et dos en pied, même échelle et même sol ; buste agrandi."""
    front = Image.open("/tmp/apose/skel.png").convert("RGB")
    back = Image.open("/tmp/apose/skel_180.png").convert("RGB")
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    fb, bb = content_box(front), content_box(back)
    top = min(fb[1], bb[1]) - 60      # le sommet du crâne est au-dessus du nez
    bottom = max(fb[3], bb[3]) + 30   # les semelles sous les chevilles
    scale = min((H * 0.92) / (bottom - top), (PANEL * 0.94) / max(fb[2] - fb[0], bb[2] - bb[0]))
    for k, (img, box) in enumerate(((front, fb), (back, bb))):
        cx = (box[0] + box[2]) / 2
        crop = img.crop((int(cx - PANEL / scale / 2), top, int(cx + PANEL / scale / 2), bottom))
        crop = crop.resize((PANEL, int((bottom - top) * scale)))
        canvas.paste(crop, (k * PANEL, H - crop.height - int(H * 0.04)))
    # buste : de la tête à la poitrine, sur toute la hauteur de la case
    fh = fb[3] - top
    cx = (fb[0] + fb[2]) / 2
    bust_h = fh * 0.34
    bust_w = bust_h * PANEL / H
    crop = front.crop((int(cx - bust_w / 2), top, int(cx + bust_w / 2), int(top + bust_h))).resize((PANEL, H))
    canvas.paste(crop, (2 * PANEL, 0))
    path = out / "layout_skeleton.png"
    canvas.save(path)
    return path


SHEET = ("A professional character reference sheet of one and the same person: <image1> gives only the face, hair, "
         "skin tone and age; <image2> gives the body and the exact outfit, every garment, colour, material and detail "
         "unchanged, and the person wears that outfit in all three panels, the portrait included. "
         "Three panels side by side, laid out exactly like <image3>, which only gives the composition, pose, scale and "
         "framing and does not change the person's appearance: on the left, a full-body front view; in the centre, a "
         "full-body back view; on the right, a large head-and-shoulders portrait facing the camera. In both full-body "
         "views the person stands in the same neutral A-pose, arms straight and held about forty-five degrees out to "
         "the sides, legs slightly apart, the whole figure from the top of the head to the soles of the shoes inside "
         "the panel, at the same size and on the same ground line. Plain white seamless studio background, bright soft "
         "even light so the colours, fabrics and seams read clearly. Photorealistic, sharp focus, natural hands.")

MANNEQUIN = ("Three panels side by side on a plain white seamless background, laid out exactly like the skeletons in "
             "<image1>: on the left, a full-body front view; in the centre, a full-body back view; on the right, a "
             "large head-and-shoulders bust facing the camera. The figure is a plain smooth white featureless "
             "mannequin with no face, no hair and no clothes, standing in a neutral A-pose that follows the skeletons "
             "exactly: arms straight and held about forty-five degrees out to the sides, legs slightly apart. The same "
             "size and ground line in both full-body panels. Soft even studio light. The image shows the mannequin "
             "alone, without lines or dots.")

print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)


def run(name, **kw):
    t0 = time.time()
    qwen21.generate(dest=out / f"{name}.png", size=(W, H), resolution=1056, **kw)
    print(f"{name} : {time.time() - t0:.0f} s", flush=True)


# le portrait verrouillé porte un t-shirt au col : le visage seul, au-dessus du col
head = out / "face_head.png"
Image.open(face).crop((230, 0, 794, 680)).save(head)
face = head
skel = out / "layout_skeleton.png"
for seed in (75, 76):
    run(f"sheet_skel_{seed}", prompt=SHEET, refs=[face, apose, skel], seed=seed)
    run(f"sheet_mannequin_{seed}", prompt=SHEET, refs=[face, apose, out / "mannequin.png"], seed=seed)
