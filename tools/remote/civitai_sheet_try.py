"""Le workflow Civitai 2960890 (« Qwen Image 2.1 Character Reference Sheet
Generator », nikhilprasanth) rejoué sur Essai atelier, ses prompts lus
dans son JSON (gardé hors du dépôt, /tmp/civitai) :

  garde-robe  le plein pied validé → planche de garde-robe (base, 30 pas,
              CFG 3,5, négatif) ;
  planche     <image1> mise en page mannequin, <image2> le visage,
              <image3> la garde-robe — leur mannequin bras le long du
              corps, puis notre mannequin en A-pose ; base 1344 × 768 comme
              eux, base 1920 × 1088, turbo 1920 × 1088."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, qwen21

src = Path("/tmp/civitai")
wf = json.loads((src / "Character Sheets.json").read_text(encoding="utf-8"))
nodes = {n["id"]: n for n in wf["nodes"]}
WARD_P, WARD_N = nodes[478]["widgets_values"][1:3]
SHEET_P, SHEET_N = nodes[515]["widgets_values"][1:3]

root = Path.home() / "Character_Factory/projects/essai-atelier"
face = root / "face/locked.png"
fullbody = root / "costumes/veryday/fullbody.png"
theirs = src / "Mannequin.png"
ours = Path("/tmp/sheet/mannequin.png")
out = Path("/tmp/civ")
out.mkdir(parents=True, exist_ok=True)
BASE = (30, 3.5)

print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)


def run(name, **kw):
    t0 = time.time()
    qwen21.generate(dest=out / f"{name}.png", **kw)
    print(f"{name} : {time.time() - t0:.0f} s", flush=True)


run("wardrobe", prompt=WARD_P, negative=WARD_N, refs=[fullbody], seed=81, size=(1344, 768), resolution=0, base=BASE)
ward = out / "wardrobe.png"
for name, layout, size, mode in (
        ("theirs_base_1344", theirs, (1344, 768), BASE),
        ("apose_base_1344", ours, (1344, 768), BASE),
        ("apose_base_1920", ours, (1920, 1088), BASE),
        ("apose_turbo_1920", ours, (1920, 1088), None)):
    run(name, prompt=SHEET_P, negative=SHEET_N if mode else "", refs=[layout, face, ward], seed=91, size=size,
        resolution=0 if mode else 1056, base=mode)
