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

from factory.gltf import GLB  # noqa: E402
from factory.rigcheck import clip_pose, skinned  # noqa: E402  (le rejeu du skinning glTF)

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
        os.environ.update(FACTORY_H3="comfyui", FACTORY_FACE_ENGINE="h3", FACTORY_FULLBODY_ENGINE="h3", FACTORY_COMFYUI_URL=f"http://127.0.0.1:{port}",
                          FACTORY_COMFYUI_URL_H3=f"http://127.0.0.1:{port}",
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
        usine("planche", "test-pilote", "--moteur-planche", "h3")
        usine("planche-ok", "test-pilote", "s001")
        usine("pose", "test-pilote", "--variantes", "1")
        usine("pose-ok", "test-pilote", "1")
        usine("vues", "test-pilote", "--sans-34", "--methode", "per_view")
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


def studio_route(tmp: Path) -> None:
    """Le studio, par son API, comme la page s'en sert : création depuis
    la conversation, puis chaque étage mis en file jusqu'au rig accepté,
    les refus du brief, les fichiers servis, et le relais du modèle de
    texte contre tools/mock_llm.py."""
    import io
    import socket
    import time
    import urllib.error
    import urllib.request

    from PIL import Image

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port, llm_port = free_port(), free_port()
    env = {**os.environ, "FACTORY_PROJECTS": str(tmp / "studio"), "FACTORY_LLM_URL": f"http://127.0.0.1:{llm_port}",
           "FACTORY_LLM_MODEL": "modele-du-studio", "PYTHONIOENCODING": "utf-8", "FACTORY_STUB_DELAY": "0.5"}
    procs = [subprocess.Popen([sys.executable, str(REPO / "tools/mock_llm.py"), str(llm_port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
             subprocess.Popen([sys.executable, "-m", "factory", "studio", "--hote", "127.0.0.1", "--port", str(port)],
                              cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    base = f"http://127.0.0.1:{port}"

    def call(path: str, body=None, method: str | None = None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, res.headers.get("Content-Type", ""), res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    def js(path: str, body=None, method: str | None = None):
        code, _, raw = call(path, body, method)
        return code, json.loads(raw or b"null")

    def run(slug: str, action: str, **params) -> dict:
        code, out = js(f"/api/characters/{slug}/actions/{action}", params)
        if code != 200:
            return {"status": "http", "error": out}
        if "job" not in out:
            return {"status": "done", **out}
        for _ in range(600):
            _, job = js(f"/api/jobs/{out['job']['id']}")
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        return {"status": "timeout"}

    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/system", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        conversation = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 5000}},
            {"type": "text", "text": "une contrebandière"}]}]
        code, out = js("/api/characters", {"sheet": {"character_name": "Ilse Varga", "role": "contrebandière"},
                                           "notes": ["cicatrice"], "conversation": conversation})
        slug = out.get("slug")
        manifest = json.loads((tmp / "studio" / "ilse-varga" / "project.json").read_text(encoding="utf-8"))
        chat = json.dumps(manifest.get("identity_chat"), ensure_ascii=False)
        check("studio : personnage créé depuis la conversation, images retirées de l'historique",
              code == 201 and slug == "ilse-varga" and manifest["identity"]["role"] == "contrebandière"
              and "AAAA" not in chat and "[image de référence]" in chat)

        early = run(slug, "fullbody", costume="x")
        check("studio : pas de plein pied sans costume ni visage — refusé dans le travail",
              early["status"] == "error" and "refusé" in (early.get("error") or ""), str(early.get("error")))
        faces = run(slug, "face", variants=2, seed=11)
        locked = run(slug, "face_lock", candidate="2")
        again = run(slug, "face_lock", candidate="1")
        check("studio : deux variantes en un travail, visage verrouillé une seule fois",
              faces["status"] == "done" and len(faces["result"]["made"]) == 2 and locked["status"] == "done"
              and again["status"] == "http" and "déjà verrouillé" in again["error"]["error"]["message"])

        # Nom d'abord, puis un brief : la fiche se remplit du visuel, et une
        # tenue écrite pendant le rendu n'est pas écrasée par le calcul.
        code, made = js("/api/characters", {"name": "Kévin Essai"})
        kid = made.get("slug")
        code, queued = js(f"/api/characters/{kid}/actions/face", {"brief": "vingt ans, coupe courte", "variants": 4})
        empty, _ = js(f"/api/characters/{kid}/actions/costume_add", {"brief": "  "})
        code, added = js(f"/api/characters/{kid}/actions/costume_add", {"brief": "hoodie bleu, baggy blanc"})
        for _ in range(600):
            if js(f"/api/jobs/{queued['job']['id']}")[1]["status"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        _, k = js(f"/api/characters/{kid}")
        kc = k["character"]
        check("studio : créé par son nom, le brief remplit la fiche, portrait en gros plan, tenue écrite pendant le "
              "rendu gardée, tenue vide refusée",
              kc["identity"].get("face_description") == "vingt ans, coupe courte" and len(kc["face"]["variations"]) == 4
              and len({c["desc"] for c in kc["face"]["candidates"]}) == 4 and "tenue-1" in kc["costumes"]
              and kc["costumes"]["tenue-1"]["brief"] == "hoodie bleu, baggy blanc" and code == 200 and empty == 409
              and "Close-up portrait photograph" in json.loads((tmp / "studio" / kid / "face" / "cand-001.json")
                                                               .read_text(encoding="utf-8"))["prompt"],
              f"{len(kc['face']['candidates'])} propositions, tenues {list(kc['costumes'])}")

        # « Autour de celui-ci » avec une puce de direction : la page envoie une
        # clé, le studio y met la phrase ; une clé inconnue ne change rien.
        older = run(kid, "face", around=1, variants=1, direction="older")
        odd = run(kid, "face", around=1, variants=1, direction="n'importe quoi")
        _, k = js(f"/api/characters/{kid}")
        descs = [c["desc"] for c in k["character"]["face"]["candidates"]]
        check("studio : autour d'un visage, la direction choisie s'ajoute à sa description, une inconnue est ignorée",
              older["status"] == "done" and odd["status"] == "done"
              and descs[-2] == f"{descs[0]}, noticeably older, with the marks of age on the face" and descs[-1] == descs[0],
              str(descs[-2:]))

        buf = io.BytesIO()
        Image.new("RGB", (64, 96), (120, 90, 60)).save(buf, "PNG")
        code, _, raw = call("/api/uploads", raw=buf.getvalue())
        upload = json.loads(raw)["id"]
        bad, _, _ = call("/api/uploads", raw=b"pas une image")
        steps = [run(slug, "costume_add", name="Voyage", prompt="long manteau de cuir", refs=[upload]),
                 run(slug, "fullbody", costume="voyage", variants=2),
                 run(slug, "fullbody_ok", costume="voyage", candidate="1", autopilot=False),
                 run(slug, "presentation", costume="voyage", redo=["expressions", "details"]),
                 run(slug, "apose", costume="voyage", variants=2),
                 run(slug, "apose_ok", costume="voyage", candidate="2"),
                 run(slug, "sheet", costume="voyage", variants=1),
                 run(slug, "sheet_ok", costume="voyage", id="s001"),
                 run(slug, "views", costume="voyage", method="orbit"),
                 run(slug, "prep", costume="voyage"),
                 run(slug, "check", costume="voyage"),
                 run(slug, "mesh", costume="voyage"),
                 run(slug, "rig", costume="voyage"),
                 run(slug, "rig_ok", costume="voyage", verdict="accepte")]
        _, detail = js(f"/api/characters/{slug}")
        stages = {s["id"]: s["state"] for s in detail["summary"]["stages"]}
        cos = detail["character"]["costumes"]["voyage"]
        failed = [(i, s.get("error")) for i, s in enumerate(steps) if s["status"] != "done"]
        check("studio : du costume au rig accepté, un travail par étage, la file vide ensuite",
              not failed and bad == 409 and len(cos["refs"]) == 1 and cos["apose"]["validated"]
              and all(stages[k] == "done" for k in ("face", "costumes", "fullbody", "pose", "sheet", "views", "mesh", "rig"))
              and detail["summary"]["next"] is None, str(failed or stages))
        _, listing = js("/api/characters")
        ilse = next(c for c in listing["characters"] if c["slug"] == slug)
        check("studio : l'affiche du casting est la planche de présentation, sans voix ni question en attente",
              ilse["poster"] == f"/files/{slug}/costumes/voyage/presentation/sheet.png" and ilse["voice"] is None
              and ilse["attention"] == 0, str({k: ilse.get(k) for k in ("poster", "voice", "attention")}))

        # L'autopilote par le studio : Cal valide un plein pied, la machine
        # fait le reste ; un travail de Cal demandé pendant ce temps passe
        # devant les étapes de l'autopilote.
        run(slug, "costume_add", name="Soir", prompt="robe noire")
        run(slug, "fullbody", costume="soir", variants=1)
        _, busy = js(f"/api/characters/{kid}/actions/face", {"variants": 3})
        code, ok = js(f"/api/characters/{slug}/actions/fullbody_ok", {"costume": "soir", "candidate": "1"})
        _, mine = js(f"/api/characters/{kid}/actions/face", {"variants": 1})
        for _ in range(1200):
            _, detail = js(f"/api/characters/{slug}")
            pilot = detail["character"]["costumes"]["soir"].get("autopilot") or {}
            if pilot.get("state") != "running":
                break
            time.sleep(0.05)
        _, jobs = js(f"/api/jobs?slug={slug}")
        auto = [j for j in jobs["jobs"] if j["action"] == "autopilot" and j["params"].get("costume") == "soir"]
        _, mine = js(f"/api/jobs/{mine['job']['id']}")
        cos = detail["character"]["costumes"]["soir"]
        first = min(j["run_order"] for j in auto) if auto else 0
        check("studio : plein pied validé, l'autopilote mène seul A-pose, vues, contrôle, mesh et rig, validés par "
              "la mesure ; le travail de Cal passe devant",
              code == 200 and ok["result"]["autopilot"] == "soir" and pilot.get("state") == "done"
              and cos["apose"]["validated_by"] == "auto" and cos["views"]["check"]["validated_by"] == "auto"
              and cos["rigs"][-1]["verdict"] == "accepted" and all(j["auto"] for j in auto)
              and mine["status"] == "done" and mine["run_order"] < first and detail["summary"]["next"] is None,
              f"état {pilot.get('state')}, {len(auto)} étapes, Cal parti {mine.get('run_order')}ᵉ, "
              f"autopilote {first}ᵉ ; {pilot.get('log', [{}])[-1].get('message')}")
        _, waiting = js("/api/attention")
        review = [a for a in waiting["attention"] if a["slug"] == slug and a["kind"] == "rig_review"]
        code, _ = js(f"/api/characters/{slug}/actions/attention", {"id": review[0]["id"] if review else "", "do": "dismiss"})
        _, after = js("/api/attention")
        check("studio : ce qui attend Cal, listé pour tous les personnages et rangé d'un clic",
              len(review) == 1 and code == 200 and review[0]["costume"] == "soir"
              and not any(a["id"] == review[0]["id"] for a in after["attention"]),
              f"{len(waiting['attention'])} entrées")

        code, ctype, img = call(f"/files/{slug}/{detail['character']['face']['locked']}")
        outside = [call(p)[0] for p in (f"/files/{slug}/../../identite.json", "/factory.local.json",
                                        "/files/.uploads/" + upload, "/factory/studio.py")]
        page, _, html = call("/")
        check("studio : fichiers du personnage servis, rien hors du personnage ni du site",
              code == 200 and ctype == "image/png" and img[:4] == b"\x89PNG" and outside == [404] * 4
              and page == 200 and b"studio.js" in html, str(outside))

        code, listing = js(f"/api/characters/{slug}/tree")
        paths = {f["path"] for f in listing.get("files", [])} if code == 200 else set()
        refused = [call(p)[0] for p in ("/api/characters/.uploads/tree", "/api/characters/..%2F..%2Ffactory/tree",
                                         "/api/characters/inconnu/tree")]
        cz, _, cz_html = call("/coulisses.html")
        check("studio : les coulisses — le dossier listé avec tailles et dates, rien hors d'un personnage",
              code == 200 and "project.json" in paths and detail["character"]["face"]["locked"] in paths
              and any(p.endswith("/mesh.json") for p in paths)
              and all({"size", "mtime"} <= set(f) for f in listing["files"]) and refused == [404] * 3
              and cz == 200 and b"coulisses.js" in cz_html, f"{len(paths)} fichiers, refus {refused}")

        _, models = js("/v1/models")
        _, reply = js("/v1/chat/completions", {"model": "local-model", "messages": [{"role": "user", "content": "x"}]})
        seen = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{llm_port}/__seen").read())
        check("studio : relais du modèle de texte, le modèle imposé par le studio",
              [m["id"] for m in models["data"]] == ["modele-du-studio"] and seen[-1]["model"] == "modele-du-studio"
              and reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "update_character_sheet")
    finally:
        for proc in procs:
            proc.terminate()


def autopilot_route(root: Path) -> None:
    """L'autopilote en direct, sans studio : une A-pose que la mesure
    refuse trois tours de suite appelle Cal, qui choisit ; un studio
    relancé reprend l'autopilote où il en était et le mène au rig accepté ;
    un mesh qui échoue deux fois appelle Cal, sa relance aboutit."""
    import contextlib
    import io
    import time

    from factory import autopilot, studio as studio_mod
    from factory.project import Project

    quiet = lambda: contextlib.redirect_stdout(io.StringIO())  # noqa: E731
    usine("costume", "test-pilote", "ville", "--prompt", "costume sombre")
    usine("pleinpied", "test-pilote", "--costume", "ville", "--variantes", "1")
    usine("pleinpied-ok", "test-pilote", "1", "--costume", "ville")

    def drive(p: Project) -> dict:
        for _ in range(60):
            with quiet():
                r = autopilot.run_step(p, "ville", lambda a, b: None)
            if not r["continue"]:
                return r
        return {"state": "boucle"}

    os.environ["FACTORY_STUB_FAIL"] = "apose"
    p = Project.open(root)
    with quiet():
        autopilot.start(p, "ville", why="essai")
    r = drive(p)
    cos = p.data["costumes"]["ville"]
    item = next((a for a in autopilot.open_items(p) if a["kind"] == "apose_failed"), None)
    cands = cos["apose"]["candidates"]
    chosen = [o for o in (item or {}).get("options", []) if o["action"] == "choose"]
    check("autopilote : A-pose bras ballants refusée par la mesure trois tours de suite, Cal appelé avec le choix",
          r["state"] == "failed" and item is not None and len(cands) == 2 * autopilot.APOSE_ROUNDS
          and all(not c["measure"]["ok"] and any("bras" in f for f in c["measure"]["fails"]) for c in cands)
          and len(chosen) == 4 and not cos["apose"].get("validated"),
          f"{len(cands)} propositions, état {r['state']}, {len(chosen)} choix")
    del os.environ["FACTORY_STUB_FAIL"]

    with quiet():
        autopilot.resolve(p, item["id"], "choose", candidate=chosen[0]["candidate"])
    check("autopilote : Cal choisit, l'A-pose est validée à son nom et l'autopilote repart",
          cos["apose"]["validated_by"] == "cal" and autopilot.state(cos)["state"] == "running"
          and not autopilot.open_items(p))

    # Le studio « meurt » ici : un nouveau studio doit reprendre seul.
    with quiet():
        studio_mod.Studio()
        for _ in range(600):
            st = autopilot.state(Project.open(root).data["costumes"]["ville"])
            if st["state"] != "running":
                break
            time.sleep(0.05)
    p = Project.open(root)
    cos = p.data["costumes"]["ville"]
    chk, mesh = cos["views"]["check"], cos["meshes"][-1]
    rig = cos["rigs"][-1]
    review = [a for a in autopilot.open_items(p) if a["kind"] == "rig_review"]
    check("autopilote : repris par un studio relancé, vues, contrôle, mesh et rig validés par la mesure",
          st["state"] == "done" and chk["ok"] and chk["validated_by"] == "auto"
          and set(cos["views"]["raw"]) == {"front", "left", "back", "right", "threequarter"}
          and mesh["validated_by"] == "auto" and mesh["views_check"] == chk["at"]
          and rig["verdict"] == "accepted" and rig["validated_by"] == "auto" and rig["validated_metric"]["ok"]
          and rig["mesh"] == mesh["version"] and len(review) == 1,
          f"état {st['state']}, rig {rig['verdict']} {rig.get('validated_metric', {}).get('fails')}")

    usine("pleinpied-ok", "test-pilote", "1", "--costume", "ville")
    os.environ["FACTORY_STUB_FAIL"] = "mesh"
    p = Project.open(root)
    with quiet():
        autopilot.start(p, "ville", why="nouveau plein pied")
    r = drive(p)
    cos = p.data["costumes"]["ville"]
    ap = cos["apose"]
    item = next((a for a in autopilot.open_items(p) if a["kind"] == "mesh_failed"), None)
    check("autopilote : nouveau plein pied — A-pose choisie par la mesure, aval refait ; mesh en échec deux fois, "
          "Cal appelé",
          ap["validated_by"] == "auto" and 40 <= ap["validated_metric"]["arms_deg"]["left"] <= 50
          and ap["validated_metric"]["outfit"]["similarity"] >= 0.9 and r["state"] == "failed" and item is not None
          and autopilot.state(cos)["tries"].get("mesh") == 2 and not any(a["kind"] == "rig_review"
                                                                        for a in autopilot.open_items(p)),
          f"A-pose {ap.get('validated_metric', {}).get('arms_deg')}, état {r['state']}")
    del os.environ["FACTORY_STUB_FAIL"]
    with quiet():
        out = autopilot.resolve(p, item["id"], "retry")
    r = drive(p)
    check("autopilote : relancé par Cal, le mesh passe et le rig suit",
          out.get("autopilot") == "ville" and r["state"] == "done"
          and p.data["costumes"]["ville"]["rigs"][-1]["verdict"] == "accepted")


def orbit_selection() -> None:
    """Une orbite dont la caméra démarre lentement, comme celle de H3 :
    la silhouette en A-pose, vue sous l'azimut θ, est large de
    max(envergure·|cos θ|, largeur du torse sous θ). Le choix sur la
    largeur doit retrouver les profils, le dos et le 3/4 à quelques
    degrés près, là où le prorata se trompe de 20°."""
    from factory import imaging, prompts

    n = 124
    theta = 360.0 * (np.arange(n) / (n - 1)) ** 1.25
    r = np.radians(theta)
    torso = np.sqrt((150 * np.cos(r)) ** 2 + (110 * np.sin(r)) ** 2)
    widths = np.maximum(372 * np.abs(np.cos(r)), torso).round().astype(int).tolist()
    picks = imaging.orbit_picks(widths, {k: a for k, (a, _) in prompts.AZIMUTHS.items()})
    true = {k: float(theta[v["frame"]]) for k, v in picks.items() if not k.startswith("_")}
    err = {k: abs(chain_err(true[k], prompts.AZIMUTHS[k][0])) for k in true}
    naive = abs(chain_err(float(theta[round(90 / 360 * n)]), 90.0))
    check("orbite : frames choisies sur la silhouette, à quelques degrés près",
          max(err.values()) <= 5.0 and naive > 10.0,
          f"écarts {', '.join(f'{k} {e:.1f}°' for k, e in err.items())} ; au prorata, le profil serait à {naive:.0f}°")


def sam3d_yaw() -> None:
    """La lecture du BVH de SAM 3D Body : un bassin tourné de −90° autour
    de Y (le sujet regarde vers la gauche de l'image) doit donner la vue
    gauche, 90°, par rapport à une face à 0°."""
    from factory import sam3d

    def bvh(ry: float) -> str:
        return ("HIERARCHY\nROOT Hips\n{\n  OFFSET 0 0 0\n  CHANNELS 6 Xposition Yposition Zposition Zrotation "
                "Xrotation Yrotation\n}\nMOTION\nFrames: 1\nFrame Time: 0.041667\n"
                f"0 0.9 0 0 0 {ry}\n")

    front, left = sam3d.root_yaw(bvh(0.0)), sam3d.root_yaw(bvh(-90.0))
    az = (sam3d.SIGN * (left - front)) % 360.0
    check("SAM 3D Body : cap du bassin lu dans le BVH, vue gauche à 90°", abs(az - 90.0) < 1e-6, f"{az:.1f}°")


def unirig_to_soma() -> None:
    """La reconnaissance UniRig → SOMA, sans UniRig. UniRig nomme ses os
    bone_0… sans sens : on lui donne des squelettes anonymes, numérotés
    dans le désordre — le SOMA complet, le même tourné vers −Z, et un
    squelette réduit à la UniRig (ni doigts, ni yeux, ni mâchoire) —, pris
    sur le gabarit en A-pose agrandi de 10 % et déplacé. Chaque os
    reconnu doit porter son vrai nom SOMA, le squelette SOMA reconstruit
    doit tomber à moins d'un centimètre, le personnage tourné doit être
    remis face à +Z, et les poids d'un doigt doivent aller à la main."""
    from factory import rig_unirig, skeleton

    spec = skeleton.soma_spec()
    names, sparents = spec["names"], spec["parents"]
    moved = skeleton.soma_apose(spec) * 1.1 + np.array([0.2, 0.0, -0.1])
    reduced = ["Hips", "Spine1", "Spine2", "Chest", "Neck1", "Head", "HeadEnd", "LeftShoulder", "LeftArm",
               "LeftForeArm", "LeftHand", "RightShoulder", "RightArm", "RightForeArm", "RightHand", "LeftLeg",
               "LeftShin", "LeftFoot", "LeftToeBase", "RightLeg", "RightShin", "RightFoot", "RightToeBase"]
    rng = np.random.default_rng(3)
    report, ok = [], True
    for label, keep, yaw in (("complet", names, 0.0), ("tourné vers −Z", names, np.pi), ("réduit", reduced, 0.0)):
        kept = [names.index(n) for n in keep]
        order = [kept[0]] + list(rng.permutation(kept[1:]))       # racine d'abord, le reste mélangé
        new = {s: i for i, s in enumerate(order)}

        def up(s: int) -> int:
            s = sparents[s]
            while s >= 0 and s not in new:
                s = sparents[s]
            return s

        parents = np.array([new[up(s)] if up(s) >= 0 else -1 for s in order])
        turn = rig_unirig._yaw_matrix(yaw)
        pos = moved[order] @ turn.T
        table, found_yaw = rig_unirig.match_soma(parents, pos)
        wrong = [f"{names[order[u]]}≠{n}" for u, n in table.items() if names[order[u]] != n]
        fixed = pos @ rig_unirig._yaw_matrix(found_yaw).T
        err = float(np.abs(rig_unirig.soma_positions(spec, fixed, table) - moved).max())
        ok &= not wrong and err < 0.01 and abs(fixed - moved[order]).max() < 1e-9
        report.append(f"{label} : {len(table)} reconnus, écart {err * 100:.2f} cm{' ; ' + ', '.join(wrong) if wrong else ''}")
        if label == "complet":
            finger = new[names.index("LeftHandIndex2")]
            owner = rig_unirig.unirig_owner(parents, table, spec)
            j, w = rig_unirig.to_soma_weights(np.array([[finger, 0]]), np.array([[0.75, 0.25]]), owner)
            ok &= int(j[0, 0]) == names.index("LeftHand") and abs(float(w[0].sum()) - 1.0) < 1e-6
    check("UniRig → SOMA : os anonymes reconnus sur la topologie, face remise à +Z, doigts versés sur la main",
          ok, " | ".join(report))

    # Deux sommets voisins où le 4e et le 5e os échangent leur rang : couper
    # net ferait sauter les poids de 0,125 ; la coupe continue, de ~2ε.
    eps = 1e-3
    a = np.array([[0.3, 0.25, 0.2, 0.125 + eps, 0.125 - eps]])
    b = np.array([[0.3, 0.25, 0.2, 0.125 - eps, 0.125 + eps]])

    def dense4(row):
        top, w = rig_unirig.top_k_continuous(row, 4)
        out = np.zeros(row.shape[1])
        out[top[0]] = w[0]
        return out

    jump = float(np.abs(dense4(a) - dense4(b)).sum())
    check("UniRig : quatre influences sans saut quand deux os échangent leur rang", jump < 0.02,
          f"écart {jump:.4f}")


def remote_local() -> None:
    """Un modèle « distant » sur la machine même : DGX2 ne sait pas s'ouvrir
    de session ssh sur lui-même, la chaîne lance alors le script sans ssh,
    avec la même mise en place (dossier de travail, entrées, sorties)."""
    if os.name != "posix":
        return
    from factory import remote

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        script = tmp / "echo_entry.py"
        script.write_text("import sys, shutil\nshutil.copy(sys.argv[1], sys.argv[2])\n", encoding="utf-8")
        src = tmp / "source.txt"
        src.write_text("rig", encoding="utf-8")
        os.environ.update(FACTORY_REMOTE_UNIRIG="local", FACTORY_PYTHON_UNIRIG=sys.executable,
                          FACTORY_CWD_UNIRIG=t)
        try:
            got = remote.run("unirig", script, args=["{job}/in.txt", "{job}/out.txt"], inputs={"in.txt": src},
                             outputs=["out.txt"], workdir=tmp / "back")
        finally:
            for k in ("FACTORY_REMOTE_UNIRIG", "FACTORY_PYTHON_UNIRIG", "FACTORY_CWD_UNIRIG"):
                os.environ.pop(k, None)
        check("modèle lancé sur la machine même, sans ssh : entrées posées, sortie rapatriée",
              got["out.txt"].read_text(encoding="utf-8") == "rig" and remote.is_local("local"))


def qwen_views() -> None:
    """Les trois montages de vues par LoRA d'angle Qwen : chacun se remplit
    sans lien pendant, et chaque vue reçoit le déclencheur de son LoRA
    avec un sens de rotation — gauche et droite opposés, dos à 180°."""
    from factory import comfy, views_qwen

    ok, detail = True, []
    for m in views_qwen.METHODS:
        texts = {v: views_qwen.prompt(m, v) for v in ("left", "back", "right", "threequarter")}
        f = comfy.fill(views_qwen.workflow(m), {"prompt": texts["left"], "seed": 1}, ["source.png"])
        dangling = [k for x in f.values() for k, v in x["inputs"].items()
                    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in f]
        trigger = {"qwen21-orbit": "<orbit>", "qwen-2511": "<sks>", "qwen-2509": "镜头"}[m]
        ok &= (not dangling and all(trigger in t for t in texts.values()) and texts["left"] != texts["right"]
               and ("180" in texts["back"] or "back view" in texts["back"]))
        detail.append(f"{m} {len(f)} nœuds")
    check("vues Qwen : trois montages complets, un prompt par vue, gauche ≠ droite", ok, ", ".join(detail))


def dry_validation() -> None:
    """La validation à blanc de `./usine doctor` : elle doit voir un nœud
    absent et un fichier de poids inconnu, dans les deux formes de liste
    que rend /object_info, et laisser passer images et `{{…}}`."""
    from factory import comfy

    info = {"UNETLoader": {"input": {"required": {"unet_name": [["a.safetensors"]]}}},
            "VAELoader": {"input": {"required": {"vae_name": ["COMBO", {"options": ["v.safetensors"]}]}}},
            "LoadImage": {"input": {"required": {"image": [["x.png"]]}}},
            "KSampler": {"input": {"required": {"seed": ["INT", {}]}}}}
    good = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors"}},
            "2": {"class_type": "VAELoader", "inputs": {"vae_name": "v.safetensors"}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "pas-encore-envoyee.png"}},
            "4": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}"}}}
    bad = {**good, "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "b.safetensors"}},
           "2": {"class_type": "VAELoader", "inputs": {"vae_name": "w.safetensors"}},
           "5": {"class_type": "Trellis2ShapeStage", "inputs": {}}}
    found = comfy.validate(bad, info)
    check("doctor : validation à blanc — nœud absent et poids inconnus vus, le reste laissé passer",
          not comfy.validate(good, info) and len(found) == 3, f"{len(found)} problèmes relevés")


def chain_err(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def native_template() -> None:
    """Le workflow Ref2VA qui tourne sur la machine (nœud natif
    MiniMaxH3ReferenceToVideo, relevé sur DGX1) : prompt branché sur un
    primitif, frames calculées, une seule image sur une entrée
    extensible, sortie vidéo avec son. `./usine gabarit` doit en faire
    un gabarit que chaque étage remplit sans lien pendant."""
    from factory import comfy, gabarit

    src = json.loads((REPO / "tools/fixtures/h3_ref2va_native_api.json").read_text(encoding="utf-8"))
    wf, _, warnings = gabarit.adopt(src)
    tpl = {k: v for k, v in wf.items() if not k.startswith("_")}
    target = next(n for n in tpl.values() if n["class_type"] == "MiniMaxH3ReferenceToVideo")["inputs"]
    out = [n for n in tpl.values() if n.get("_meta", {}).get("title") == "OUT"]
    check("gabarit natif : prompt, frames, taille et graine branchés sur la chaîne",
          (target["prompt"], target["length"], target["width"], target["height"])
          == ("{{prompt}}", "{{frames}}", "{{width}}", "{{height}}")
          and any(n["inputs"].get("noise_seed") == "{{seed}}" for n in tpl.values()) and not warnings)
    check("gabarit natif : sortie en PNG sans recompression, son et calculs retirés",
          [n["class_type"] for n in out] == ["SaveImage"]
          and not any(n["class_type"] in ("VHS_VideoCombine", "VAEDecodeAudio", "ComfyMathExpression",
                                          "PrimitiveStringMultiline") for n in tpl.values()))
    counts, dangling = [], []
    for n in (0, 1, 3, 9):
        f = comfy.fill(tpl, {"prompt": "p", "seed": 1, "width": 768, "height": 1344, "frames": 5},
                       [f"r{i}.png" for i in range(n)])
        node = next(x for x in f.values() if x["class_type"] == "MiniMaxH3ReferenceToVideo")["inputs"]
        counts.append(sorted(int(k.rsplit("_", 1)[1]) for k in node if k.startswith("ref_images.")))
        dangling += [k for x in f.values() for k, v in x["inputs"].items()
                     if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in f]
    check("gabarit natif : 0 à 9 références, dans l'ordre <Picture 1…n>, sans lien pendant",
          counts == [[], [0], [0, 1, 2], list(range(9))] and not dangling, str([len(c) for c in counts]))


# ── le parcours ────────────────────────────────────────────────────

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="usine-check-"))
    os.environ["FACTORY_PROJECTS"] = str(tmp)
    for cap in ("H3", "PORTRAIT", "BRIEF", "TRELLIS", "HUNYUAN3D", "UNIRIG", "KIMODO", "SAM3DBODY"):
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
    usine("planche", "test-pilote", "--moteur-planche", "h3", "--ab", "--graine", "3")
    sheets = manifest()["costumes"]["veste"]["sheets"]
    check("A/B du disque : deux planches, même graine",
          len(sheets) == 2 and sheets[0]["seed"] == sheets[1]["seed"] and sheets[1]["mask_face"])
    usine("planche-ok", "test-pilote", "s002")
    usine("vues", "test-pilote", expect=2)
    check("pas de vues sans A-pose validée, planche ou pas", True)
    usine("pose", "test-pilote", "--variantes", "2", "--graine", "5")
    ap = manifest()["costumes"]["veste"]["apose"]
    sent = json.loads((root / "costumes/veste/apose/cand-001.json").read_text(encoding="utf-8"))
    check("A-pose : deux propositions, le plein pied validé en <image1> et le squelette en <image2>",
          len(ap["candidates"]) == 2 and (root / ap["skeleton"]).exists()
          and sent["refs"] == ["costumes/veste/fullbody.png", "costumes/veste/apose/skeleton.png"]
          and "<image2>" in sent["prompt"])
    usine("pose-ok", "test-pilote", "2")
    usine("planche", "test-pilote", "--variantes", "2", "--graine", "9")
    cos = manifest()["costumes"]["veste"]
    made = [s for s in cos["sheets"] if s.get("engine") == "qwen21"]
    from PIL import Image
    layout = Image.open(root / made[0]["layout"])
    check("planche Qwen : deux planches trois cases après l'A-pose, mise en page en squelettes 1920 × 1088",
          len(made) == 2 and layout.size == (1920, 1088) and (root / made[0]["file"]).exists()
          and "<image3>" in (root / "costumes/veste/sheets" / made[0]["id"] / "prompt.txt").read_text(encoding="utf-8"))
    usine("planche-ok", "test-pilote", made[1]["id"])

    usine("presentation", "test-pilote", "--theme", "both", "--graine", "21")
    pres = manifest()["costumes"]["veste"]["presentation"]
    ids = {g: [e["id"] for e in v] for g, v in pres["panels"].items()}
    board = Image.open(root / pres["sheet"])
    check("présentation : six expressions, cinq poses sur squelette, quatre détails, palette, 3840 × 2160 en deux fonds",
          len(ids["expressions"]) == 6 and len(ids["poses"]) == 5 and len(ids["details"]) == 4
          and board.size == (3840, 2160) and set(pres["variants"]) == {"clair", "sombre"}
          and 2 <= len(pres["palette"]) <= 6
          and all((root / e["cut"]).exists() and (root / e["skeleton"]).exists() for e in pres["panels"]["poses"])
          and all(e["engine"] == "krea2" and e["prompt"].startswith("Change only the person's facial expression")
                  for e in pres["panels"]["expressions"])
          and all(e["engine"] == "krea2" and e["prompt"].startswith("Full-body fashion photograph")
                  and "The person stands in a relaxed natural pose" not in e["prompt"]
                  for e in pres["panels"]["poses"]),
          f"{ids} · palette {pres['palette']}")
    seeds = {e["id"]: e["seed"] for v in pres["panels"].values() for e in v}
    usine("presentation", "test-pilote", "--refaire", "joie,marche", "--graine", "900")
    again = manifest()["costumes"]["veste"]["presentation"]
    changed = {e["id"] for v in again["panels"].values() for e in v if e["seed"] != seeds[e["id"]]}
    out = usine("presentation", "test-pilote", "--refaire", "inconnue", expect=2)
    check("présentation : --refaire ne refait que les cases nommées, une case inconnue est refusée",
          changed == {"joie", "marche"} and "case inconnue" in out, str(sorted(changed)))

    usine("vues", "test-pilote")
    raw = manifest()["costumes"]["veste"]["views"]["raw"]
    left = json.loads((root / "costumes/veste/views/raw/left.json").read_text(encoding="utf-8"))
    check("quatre vues orthogonales et le 3/4, chacune guidée par le squelette tourné à son angle",
          set(raw) == {"front", "left", "back", "right", "threequarter"}
          and left["refs"] == ["costumes/veste/apose.png", "costumes/veste/views/raw/skeleton_090.png"]
          and raw["front"]["azimuth_source"] == "A-pose validée")
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
    albedo = json.loads((v1 / "mesh.json").read_text(encoding="utf-8")).get("albedo") or {}
    check("albedo repris de la face et du dos, couleurs des voxels gardées à côté",
          (v1 / "model_voxels.glb").exists() and set(albedo.get("fit_iou", {})) == {"front", "back"}
          and albedo.get("from_views", 0) > 0.1, json.dumps(albedo.get("fit_iou")) + f" · {albedo.get('from_views')}")
    from factory import comfy as comfy_mod, mesh_comfy
    tpl = comfy_mod.load_template("trellis2_mv.json")
    wf = mesh_comfy.keep_views(comfy_mod.fill(tpl, {"seed": 1, "faces": 50000}, ["f.png", "b.png"]), ("front", "back"))
    pix = next(n for n in wf.values() if n["class_type"] == "Pixal3DMultiViewConditioning")
    loads = sorted(n["inputs"]["image"] for n in wf.values() if n["class_type"] == "LoadImage")
    check("mesh ComfyUI : face et dos seuls, sans nœud orphelin",
          sorted(k for k in ("front", "left", "back", "right") if k in pix["inputs"]) == ["back", "front"]
          and loads == ["b.png", "f.png"]
          and sum(n["class_type"] == "RemoveBackground" for n in wf.values()) == 2, f"{len(wf)} nœuds")

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

    autopilot_route(root)
    studio_route(tmp)
    comfy_route(tmp, ident)
    native_template()
    orbit_selection()
    sam3d_yaw()
    dry_validation()
    unirig_to_soma()
    remote_local()
    qwen_views()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} vérifications passées\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
