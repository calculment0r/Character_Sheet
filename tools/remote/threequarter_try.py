"""Les 3/4 d'Essai atelier avec le squelette à tête en cylindre
(apose_skeleton.py à jour, copié en /tmp/apose_skeleton_new.py), chaque
rendu mesuré par SAM 3D Body contre la face (l'A-pose validée)."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Character_Factory"))
from factory import memory, pose, qwen21, sam3d

root = Path.home() / "Character_Factory/projects/essai-atelier/costumes/veryday"
front = root / "apose.png"
out = Path("/tmp/tq")
out.mkdir(parents=True, exist_ok=True)
subprocess.run([str(Path.home() / "comfyui-env/bin/python"), "/tmp/apose_skeleton_new.py", str(front),
                str(out / "skel.png"), "1344", "1792", "--vues=45,315"], check=True, capture_output=True)
print("texte déchargé :", memory.unload_llm(), flush=True)
for url in memory.comfy_instances():
    memory.free_comfy(url)
time.sleep(5)
views = {"front": front}
for az in (45, 315):
    for seed in (103, 104):
        dest = out / f"v{az:03d}_{seed}.png"
        t0 = time.time()
        qwen21.generate(prompt=pose.text_view(az), refs=[front, out / f"skel_{az:03d}.png"], dest=dest, seed=seed,
                        size=pose.SIZE, resolution=pose.RESOLUTION)
        print(f"{dest.name} : {time.time() - t0:.0f} s", flush=True)
        views[dest.stem] = dest
got = sam3d.measure_azimuths(views, workdir=out / ".sam3d")
for n, m in got.items():
    print(f"{n:12s} mesuré {m['azimuth']:6.1f}°  (cap brut {m['yaw_raw']})")
