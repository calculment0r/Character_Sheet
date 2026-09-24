"""Vérification de la chaîne locale, de bout en bout, sans GPU.

    python3 tools/chain_check.py          # moteurs factices, dossier temporaire

Crée un personnage, le mène du visage à la timeline cuite par `./usine`,
et vérifie à chaque étage ce que la chaîne promet : les fichiers, le
manifeste, les refus que le brief impose, et — en rejouant le skinning
glTF comme le ferait un viewer — que le rig déforme le mesh comme il
faut : bras levés au-dessus de la tête, accroupi plus bas que debout,
racine continue d'une prise à l'autre.

Rend 0 si tout passe, 1 sinon.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8")   # Windows : une sortie redirigée serait en cp1252

import numpy as np  # noqa: E402

from factory.gltf import GLB, slerp, trs_matrix  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'ok    ' if ok else 'ÉCHEC '} {name}{f' — {detail}' if detail else ''}")


def usine(*args: str, expect: int = 0) -> str:
    res = subprocess.run([sys.executable, "-m", "factory", *args], cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", env=os.environ)
    if res.returncode != expect:
        print(res.stdout, res.stderr)
        raise SystemExit(f"./usine {' '.join(args)} → {res.returncode}, attendu {expect}")
    return res.stdout + res.stderr


# ── rejeu du skinning glTF ─────────────────────────────────────────

def _pose_overrides(g: GLB, anim: dict, t: float) -> dict:
    out = {}
    for ch in anim["channels"]:
        smp = anim["samplers"][ch["sampler"]]
        times = g.read_accessor(smp["input"]).reshape(-1)
        vals = g.read_accessor(smp["output"])
        i = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 1))
        j = min(i + 1, len(times) - 1)
        a = 0.0 if j == i else float((t - times[i]) / (times[j] - times[i]))
        path = ch["target"]["path"]
        v = slerp(vals[i], vals[j], a) if path == "rotation" else vals[i] * (1 - a) + vals[j] * a
        out[(path, ch["target"]["node"])] = v
    return out


def skinned(g: GLB, overrides: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Positions des sommets déformés, et position monde de chaque nœud."""
    nodes = g.doc["nodes"]
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    cache: dict[int, np.ndarray] = {}

    def world(i: int) -> np.ndarray:
        if i not in cache:
            n = nodes[i]
            m = trs_matrix(overrides.get(("translation", i), n.get("translation", [0, 0, 0])),
                           overrides.get(("rotation", i), n.get("rotation", [0, 0, 0, 1])),
                           n.get("scale", [1, 1, 1]))
            cache[i] = world(parent[i]) @ m if i in parent else m
        return cache[i]

    skin = g.doc["skins"][0]
    ibm = g.read_accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    joint_m = np.stack([world(j) @ ibm[k] for k, j in enumerate(skin["joints"])])
    verts = []
    for mesh in g.doc["meshes"]:
        for prim in mesh["primitives"]:
            p = g.read_accessor(prim["attributes"]["POSITION"]).astype(float)
            jj = g.read_accessor(prim["attributes"]["JOINTS_0"]).astype(int)
            ww = g.read_accessor(prim["attributes"]["WEIGHTS_0"]).astype(float)
            m = np.einsum("vk,vkij->vij", ww, joint_m[jj])
            verts.append(np.einsum("vij,vj->vi", m, np.c_[p, np.ones(len(p))])[:, :3])
    where = {nodes[j].get("name", str(j)): world(j)[:3, 3] for j in skin["joints"]}
    return np.vstack(verts), where


def clip_pose(g: GLB, name: str) -> tuple[np.ndarray, dict]:
    anim = next(a for a in g.doc["animations"] if a["name"] == name)
    times = g.read_accessor(anim["samplers"][0]["input"]).reshape(-1)
    return skinned(g, _pose_overrides(g, anim, float(times[-1])))


# ── le trajet H3 → ComfyUI ─────────────────────────────────────────

def comfy_route(tmp: Path, ident: Path) -> None:
    """Le vrai client ComfyUI, contre tools/mock_comfy.py : un workflow
    exporté est adopté par `./usine gabarit`, puis visage, plein pied,
    planche et vues passent par lui."""
    import socket
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, str(REPO / "tools/mock_comfy.py"), str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=1)
                break
            except OSError:
                subprocess.run([sys.executable, "-c", "import time; time.sleep(0.1)"])
        os.environ.update(FACTORY_H3="comfyui", FACTORY_COMFYUI_URL=f"http://127.0.0.1:{port}",
                          FACTORY_WORKFLOWS=str(tmp / "workflows"), FACTORY_PROJECTS=str(tmp / "comfy"))
        out = usine("gabarit", str(REPO / "tools/fixtures/h3_export_api.json"))
        check("gabarit : références, prompt, graine, taille, frames et sortie marqués",
              all(x in out for x in ("REF 3", "{{prompt}}", "{{seed}}", "{{frames}}", "{{width}}", "→ OUT")))
        usine("nouveau", "--identite", str(ident))
        usine("visage", "test-pilote", "--variantes", "1")
        usine("visage-ok", "test-pilote", "1")
        usine("costume", "test-pilote", "veste")
        usine("pleinpied", "test-pilote", "--variantes", "1")
        usine("pleinpied-ok", "test-pilote", "1")
        usine("planche", "test-pilote")
        usine("planche-ok", "test-pilote", "s001")
        usine("vues", "test-pilote", "--sans-34")
        root = tmp / "comfy" / "test-pilote"
        meta = json.loads((root / "costumes/veste/views/raw/left.json").read_text(encoding="utf-8"))
        check("H3 par ComfyUI : cinq frames rendues, la plus nette gardée",
              meta["backend"] == "comfyui" and meta["frames_returned"] == 5 and meta["frame_kept"] == 2)
        sent = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/__prompts").read())
        refs = [sum(1 for k in wf["5"]["inputs"] if k.startswith("image_")) for wf in sent]
        check("chaque étage envoie ses références nommées : 0, 1, 2, puis 3 par vue",
              refs == [0, 1, 2, 3, 3, 3, 3], str(refs))
        sizes = {(wf["5"]["inputs"]["width"], wf["5"]["inputs"]["height"]) for wf in sent}
        check("768 px de petit côté partout", all(min(s) == 768 for s in sizes), str(sorted(sizes)))
    finally:
        server.terminate()


# ── le parcours ────────────────────────────────────────────────────

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="usine-check-"))
    os.environ["FACTORY_PROJECTS"] = str(tmp)
    for cap in ("H3", "TRELLIS", "HUNYUAN3D", "UNIRIG", "KIMODO", "SAM3DBODY"):
        os.environ[f"FACTORY_{cap}"] = "stub"
    os.environ["FACTORY_PREP"] = "builtin"
    os.environ["FACTORY_DELIGHT"] = "off"
    print(f"\nCharacter Factory — chaîne locale, moteurs factices, dans {tmp}\n")

    ident = tmp / "identite.json"
    ident.write_text(json.dumps({"schema": "character-factory/identity@1",
                                 "sheet": {"character_name": "Test Pilote", "age": "forty", "gender": "woman"},
                                 "notes": ["col relevé"]}), encoding="utf-8")
    usine("nouveau", "--identite", str(ident))
    root = tmp / "test-pilote"
    manifest = lambda: json.loads((root / "project.json").read_text(encoding="utf-8"))  # noqa: E731
    check("personnage créé depuis l'export de la page", manifest()["identity"]["character_name"] == "Test Pilote")

    usine("visage", "test-pilote", "--variantes", "3", "--graine", "7")
    check("trois variantes de visage", len(manifest()["face"]["candidates"]) == 3)
    usine("pleinpied", "test-pilote", expect=2)
    check("pas de plein pied sans visage verrouillé", True)
    usine("visage-ok", "test-pilote", "2")
    out = usine("visage-ok", "test-pilote", "1", expect=2)
    check("le visage ne se verrouille qu'une fois", "déjà verrouillé" in out)

    usine("costume", "test-pilote", "veste")
    usine("pleinpied", "test-pilote", "--variantes", "2")
    usine("planche", "test-pilote", expect=2)
    check("pas de planche sans plein pied validé", True)
    usine("pleinpied-ok", "test-pilote", "1")
    usine("planche", "test-pilote", "--ab", "--graine", "3")
    sheets = manifest()["costumes"]["veste"]["sheets"]
    check("A/B du disque : deux planches, même graine",
          len(sheets) == 2 and sheets[0]["seed"] == sheets[1]["seed"] and sheets[1]["mask_face"])
    usine("vues", "test-pilote", expect=2)
    check("pas de vues sans planche validée", True)
    usine("planche-ok", "test-pilote", "s002")

    usine("vues", "test-pilote")
    raw = manifest()["costumes"]["veste"]["views"]["raw"]
    check("quatre vues orthogonales et le 3/4", set(raw) == {"front", "left", "back", "right", "threequarter"})
    usine("mesh", "test-pilote", expect=2)
    check("pas de mesh sans contrôle d'alignement", True)
    usine("prep", "test-pilote")
    prep = json.loads((root / "costumes/veste/views/prepared/prep.json").read_text(encoding="utf-8"))
    tops = {n: v["box_after"][1] for n, v in prep["views"].items()}
    bottoms = {n: v["box_after"][3] for n, v in prep["views"].items()}
    check("vues préparées : même marge haute et même ligne de pieds",
          max(tops.values()) - min(tops.values()) <= 2 and max(bottoms.values()) - min(bottoms.values()) <= 2,
          f"haut {sorted(set(tops.values()))} bas {sorted(set(bottoms.values()))}")
    usine("controle", "test-pilote", "--angle", "left=96", expect=2)
    check("contrôle : 6° d'écart refusés", True)
    usine("controle", "test-pilote", "--angle", "left=94.5", "--angle", "front=359")
    check("contrôle : 4,5° et 359° acceptés (bouclage à 360°)", manifest()["costumes"]["veste"]["views"]["check"]["ok"])

    usine("mesh", "test-pilote")
    usine("mesh", "test-pilote", "--moteur3d", "hunyuan3d-2.1")
    meshes = manifest()["costumes"]["veste"]["meshes"]
    v1 = root / meshes[0]["dir"]
    check("deux versions de mesh, les deux moteurs",
          [m["engine"] for m in meshes] == ["trellis2", "hunyuan3d-2.1"] and meshes[1]["parent_version"] == 1)
    check("canaux PBR rangés à part",
          all((v1 / f"{c}.png").exists() for c in ("albedo", "metallic", "roughness", "normal")))

    usine("rig", "test-pilote", "--pose", "tpose", expect=2)
    check("bind en T-pose refusé", True)
    usine("rig", "test-pilote")
    rig = manifest()["costumes"]["veste"]["rigs"][0]
    g = GLB.load(root / rig["glb"])
    clips = [a["name"] for a in g.doc.get("animations", [])]
    check("rig : 77 articulations SOMA et cinq poses de contrôle",
          len(g.doc["skins"][0]["joints"]) == 77 and len([c for c in clips if c.startswith("control/")]) == 5,
          ", ".join(clips))

    bind_v, bind_j = skinned(g, {})
    check("bind en A-pose : bras à 45° environ", 40 <= rig["meta"]["arm_angle_deg"] <= 50,
          f"{rig['meta']['arm_angle_deg']}°")
    up_v, up_j = clip_pose(g, "control/arms_up")
    check("bras levés : les mains passent au-dessus de la tête",
          up_j["LeftHand"][1] > up_j["HeadEnd"][1] and up_j["RightHand"][1] > up_j["HeadEnd"][1])
    down_v, down_j = clip_pose(g, "control/arms_down")
    check("bras le long du corps : mains sous les hanches, collées au corps",
          down_j["LeftHand"][1] < down_j["Hips"][1] and abs(down_j["LeftHand"][0]) < 0.35)
    sq_v, sq_j = clip_pose(g, "control/squat")
    check("accroupi : les hanches descendent, les pieds restent au sol",
          sq_j["Hips"][1] < bind_j["Hips"][1] - 0.3 and sq_j["LeftFoot"][1] < 0.25,
          f"hanches {bind_j['Hips'][1]:.2f} → {sq_j['Hips'][1]:.2f} m")
    lengths = lambda v: np.linalg.norm(v.max(0) - v.min(0))  # noqa: E731
    check("le mesh ne se disloque dans aucune pose",
          all(lengths(v) < 2.6 for v in (bind_v, up_v, down_v, sq_v)))

    usine("prise", "test-pilote", "--kimodo", "walks forward", "--frames", "90")
    usine("prise", "test-pilote", "--video", str(root / "face/locked.png"))
    usine("prise", "test-pilote", "--main", "poing", "--frames", "30")
    usine("timeline", "test-pilote", "main", "--piste", "body:t001", "--piste", "body:t002:at=60:fondu=15",
          "--piste", "hands_r:t003:at=0:poids=1")
    usine("bake", "test-pilote", "main")
    tl = manifest()["timelines"]["main"]
    anim = np.load(root / tl["baked"]["npz"])
    r = anim["root_positions"]
    check("timeline cuite : NPZ somaskel77", anim["local_rot_mats"].shape[1:] == (77, 3, 3),
          f"{anim['local_rot_mats'].shape[0]} frames")
    check("racine continue d'une prise à l'autre", float(np.abs(np.diff(r, axis=0)).max()) < 0.1,
          f"saut max {float(np.abs(np.diff(r, axis=0)).max()):.3f} m")
    names = list(anim["joint_names"])
    fist = anim["local_quats"][10, names.index("RightHandIndex3")]
    free = anim["local_quats"][10, names.index("LeftHandIndex3")]
    check("la couche mains ne touche que sa main", abs(fist[3]) < 0.9 and abs(free[3]) > 0.99)
    gb = GLB.load(root / tl["baked"]["glb"])
    check("GLB animé : un seul clip, celui de la timeline",
          [a["name"] for a in gb.doc["animations"]] == ["timeline/main"])

    comfy_route(tmp, ident)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} vérifications passées\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
