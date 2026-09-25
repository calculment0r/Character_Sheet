"""Client ComfyUI, en local.

Si H3 tourne déjà dans ComfyUI sur la machine, c'est lui qui garde le
modèle résident : la chaîne ne fait que lui confier un workflow et
récupérer les images. Rien ne sort de la machine — ComfyUI écoute sur
127.0.0.1.

Les workflows sont des gabarits au format API de ComfyUI (menu
Workflow → Export (API)), rangés dans `workflows/`. Trois conventions
suffisent pour brancher n'importe quel workflow qui marche déjà :

  - `{{nom}}` dans une valeur texte est remplacé ; si la valeur entière
    est `{{nom}}`, elle prend le type de la donnée (entier, flottant) ;
  - les nœuds de chargement d'image titrés `REF 1`, `REF 2`… reçoivent
    les références dans l'ordre ; ceux qui restent sans image sont
    retirés, avec les entrées qui pointaient vers eux ;
  - les nœuds de sortie titrés `OUT` sont ceux dont on rapatrie les
    images. Sans titre `OUT`, on prend toutes les sorties.
"""

from __future__ import annotations

import copy
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import config

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url: str | None = None, timeout: float = 30.0) -> None:
        self.url = (url or config.comfyui_url()).rstrip("/")
        self.timeout = timeout
        # Préfixé : le studio n'interrompt que les calculs de la chaîne.
        self.client_id = f"usine-{uuid.uuid4().hex}"

    # ── transport ──────────────────────────────────────────────────

    def _req(self, method: str, path: str, *, data: bytes | None = None,
             headers: dict | None = None, raw: bool = False):
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:2000]
            raise ComfyError(f"ComfyUI {method} {path} → {exc.code} : {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ComfyError(f"ComfyUI injoignable sur {self.url} ({exc}) — est-il lancé ?") from exc
        return body if raw else json.loads(body or b"null")

    def ping(self) -> dict:
        return self._req("GET", "/system_stats")

    def object_info(self) -> dict:
        return self._req("GET", "/object_info")

    # ── envoi des références ───────────────────────────────────────

    def upload(self, path: Path) -> str:
        """Dépose une image dans le dossier input de ComfyUI ; rend le
        nom à donner au nœud LoadImage."""
        boundary = uuid.uuid4().hex
        data = path.read_bytes()
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        out = self._req("POST", "/upload/image", data=b"".join(parts),
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        name = out.get("name", path.name)
        return f"{out['subfolder']}/{name}" if out.get("subfolder") else name

    # ── exécution ──────────────────────────────────────────────────

    def queue(self, workflow: dict) -> str:
        out = self._req("POST", "/prompt", data=json.dumps({"prompt": workflow, "client_id": self.client_id}).encode(),
                        headers={"Content-Type": "application/json"})
        if out.get("node_errors"):
            raise ComfyError(f"workflow refusé : {json.dumps(out['node_errors'], ensure_ascii=False)[:2000]}")
        return out["prompt_id"]

    def wait(self, prompt_id: str, *, report=None, timeout: float = 3600.0, poll: float = 1.0) -> dict:
        """Attend la fin d'un workflow. ComfyUI ne donne la progression
        fine que par websocket ; ici on relève l'état de la file."""
        start = time.monotonic()
        seen_running = False
        while time.monotonic() - start < timeout:
            hist = self._req("GET", f"/history/{prompt_id}")
            if hist and prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[0][1].get("exception_message", "") if msgs else ""
                    raise ComfyError(f"ComfyUI a échoué : {detail}".strip())
                return entry
            if report:
                q = self._req("GET", "/queue")
                running = any(item[1] == prompt_id for item in q.get("queue_running", []))
                if running and not seen_running:
                    seen_running = True
                    report(0.2, "ComfyUI calcule")
                elif not running:
                    pending = [item[1] for item in q.get("queue_pending", [])]
                    if prompt_id in pending:
                        report(0.05, f"en file ComfyUI, rang {pending.index(prompt_id) + 1}")
            time.sleep(poll)
        raise ComfyError(f"ComfyUI n'a pas rendu le travail en {timeout:.0f} s")

    def outputs(self, entry: dict, workflow: dict, title: str = "OUT") -> list[dict]:
        """Les fichiers produits, dans l'ordre des nœuds portant ce titre
        (`OUT` par défaut ; sans nœud `OUT`, toutes les sorties)."""
        wanted = [nid for nid, node in workflow.items() if node.get("_meta", {}).get("title") == title]
        produced = entry.get("outputs", {})
        order = wanted or (list(produced) if title == "OUT" else [])
        files = []
        for nid in order:
            out = produced.get(nid, {})
            for key in ("images", "gifs", "videos", "animated", "3d"):
                for item in out.get(key, []) or []:
                    if isinstance(item, dict) and item.get("filename"):
                        files.append(item)
        return files

    def download(self, item: dict, dest: Path) -> Path:
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._req("GET", f"/view?{q}", raw=True))
        return dest

    def run(self, workflow: dict, dest_dir: Path, *, report=None, prefix: str = "out") -> list[Path]:
        return self.run_titled(workflow, dest_dir, report=report, prefixes={"OUT": prefix})["OUT"]

    def run_titled(self, workflow: dict, dest_dir: Path, *, report=None,
                   prefixes: dict[str, str]) -> dict[str, list[Path]]:
        """Lance un workflow et rapatrie les sorties de chaque titre
        demandé, `{"OUT": "orbit", "OUT MASK": "mask"}` par exemple."""
        pid = self.queue(workflow)
        if report:
            report(0.02, f"envoyé à ComfyUI ({pid[:8]})")
        entry = self.wait(pid, report=report)
        result = {}
        for title, prefix in prefixes.items():
            files = self.outputs(entry, workflow, title)
            if not files:
                raise ComfyError(f"ComfyUI a fini sans rien produire pour {title} — le workflow a-t-il ce nœud ?")
            result[title] = [self.download(item, dest_dir / f"{prefix}_{i:03d}{Path(item['filename']).suffix or '.png'}")
                             for i, item in enumerate(files)]
        return result


# ── détourage ──────────────────────────────────────────────────────

def matte_workflow(names: list[str]) -> dict:
    """BiRefNet sur des images déjà déposées dans ComfyUI : un masque en
    niveaux de gris par image, sorties `OUT` dans l'ordre."""
    wf = {"1": {"class_type": "LoadBackgroundRemovalModel",
                "inputs": {"bg_removal_name": config.setting("birefnet", "birefnet.safetensors")}}}
    for k, name in enumerate(names):
        base = 10 + 4 * k
        wf[str(base)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[str(base + 1)] = {"class_type": "RemoveBackground",
                             "inputs": {"bg_removal_model": ["1", 0], "image": [str(base), 0]}}
        wf[str(base + 2)] = {"class_type": "MaskToImage", "inputs": {"mask": [str(base + 1), 0]}}
        wf[str(base + 3)] = {"class_type": "SaveImage", "_meta": {"title": "OUT"},
                             "inputs": {"images": [str(base + 2), 0], "filename_prefix": "usine/matte"}}
    return wf


def remove_background(images: list, *, workdir: Path, batch: int = 16, report=None) -> list:
    """Les masques de BiRefNet (nœuds natifs LoadBackgroundRemovalModel
    et RemoveBackground), une image en niveaux de gris par image, dans
    l'ordre. Le workflow est construit ici : il n'y a rien à adopter.

    La polarité du masque n'est pas documentée ; on la lit sur l'image :
    le pourtour est du fond, il doit sortir noir."""
    import numpy as np
    from PIL import Image

    comfy = Comfy(config.comfyui_url("prep"))
    workdir.mkdir(parents=True, exist_ok=True)
    masks = []
    for start in range(0, len(images), batch):
        chunk = images[start:start + batch]
        names = []
        for img in chunk:
            src = workdir / f"matte_{uuid.uuid4().hex[:10]}.png"
            img.convert("RGB").save(src)
            names.append(comfy.upload(src))
            src.unlink()
        wf = matte_workflow(names)
        if report:
            report(start / len(images), f"détourage BiRefNet {start + len(chunk)}/{len(images)}")
        for path in comfy.run(wf, workdir, prefix=f"mask{start:04d}"):
            m = np.asarray(Image.open(path).convert("L"))
            path.unlink()
            rim = np.concatenate([m[0], m[-1], m[:, 0], m[:, -1]])
            masks.append(Image.fromarray(255 - m if rim.mean() > 127 else m))
    return masks


# ── validation à blanc ─────────────────────────────────────────────

def _options(spec) -> list | None:
    """Les valeurs permises d'une entrée COMBO, dans les deux formes
    que rend /object_info : [[…options]] ou ["COMBO", {"options": […]}]."""
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def validate(workflow: dict, info: dict) -> list[str]:
    """Ce que ComfyUI refuserait, sans rien exécuter : un nœud absent, ou
    une valeur de liste — un fichier de poids surtout — que le serveur ne
    connaît pas. Les images d'entrée et les `{{…}}` ne sont pas jugés."""
    problems = []
    for nid, node in workflow.items():
        if nid.startswith("_"):
            continue
        cls = node.get("class_type")
        if cls not in info:
            problems.append(f"nœud {nid} : {cls} absent de ce ComfyUI")
            continue
        required = info[cls].get("input", {}).get("required", {})
        spec = {**required, **info[cls].get("input", {}).get("optional", {})}
        inputs = node.get("inputs", {})
        # Les entrées extensibles et dynamiques s'écrivent « groupe.clé » : on ne les juge pas.
        grown = {k.split(".")[0] for k in inputs if "." in k}
        for key in required:
            if key not in inputs and key not in grown:
                problems.append(f"nœud {nid} ({cls}) : entrée obligatoire « {key} » absente")
        for key, val in inputs.items():
            if "." not in key and key not in spec:
                problems.append(f"nœud {nid} ({cls}) : entrée « {key} » inconnue de ce nœud")
                continue
            if not isinstance(val, str) or "{{" in val or key == "image":
                continue
            opts = _options(spec.get(key))
            if opts is not None and val not in opts:
                problems.append(f"nœud {nid} ({cls}).{key} : « {val} » inconnu ici")
    return problems


# ── gabarits ───────────────────────────────────────────────────────

def load_template(name: str) -> dict:
    path = config.workflows_dir() / name
    if not path.is_file():
        raise ComfyError(f"gabarit introuvable : {path}")
    wf = json.loads(path.read_text(encoding="utf-8"))
    # Un export « Save » (format éditeur) n'a pas la forme attendue.
    if "nodes" in wf and "links" in wf:
        raise ComfyError(f"{path.name} est au format éditeur : réexporte-le avec Workflow → Export (API)")
    return {k: v for k, v in wf.items() if not k.startswith("_")}


def fill(template: dict, values: dict, refs: list[str]) -> dict:
    """Remplit un gabarit : les `{{nom}}`, puis les nœuds REF n."""
    wf = copy.deepcopy(template)

    def sub(v):
        if isinstance(v, str):
            m = PLACEHOLDER.fullmatch(v)
            if m and m.group(1) in values:
                return values[m.group(1)]
            return PLACEHOLDER.sub(lambda mm: str(values.get(mm.group(1), mm.group(0))), v)
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v

    wf = {nid: {**node, "inputs": sub(node.get("inputs", {}))} for nid, node in wf.items()}

    ref_nodes = sorted(
        (int(m.group(1)), nid) for nid, node in wf.items()
        if (m := re.fullmatch(r"REF (\d+)", node.get("_meta", {}).get("title", "")))
    )
    removed = set()
    for i, (_, nid) in enumerate(ref_nodes):
        if i < len(refs):
            wf[nid]["inputs"]["image"] = refs[i]
        else:
            removed.add(nid)
    if len(refs) > len(ref_nodes):
        raise ComfyError(f"le gabarit n'a que {len(ref_nodes)} nœuds REF pour {len(refs)} références")

    # Retire les nœuds sans image, puis tout ce qui ne vivait que d'eux :
    # un nœud qui perd un lien et n'en garde aucun autre (un redimension-
    # nement branché sur REF 3, par exemple) n'a plus rien à traiter.
    def is_link(val) -> bool:
        return isinstance(val, list) and len(val) == 2 and isinstance(val[0], str)

    changed = True
    while changed:
        changed = False
        for nid in list(wf):
            if nid in removed:
                del wf[nid]
                changed = True
                continue
            inputs = wf[nid].get("inputs", {})
            lost = [k for k, v in inputs.items() if is_link(v) and v[0] in removed]
            for key in lost:
                del inputs[key]
            if lost:
                changed = True
                if not any(is_link(v) for v in inputs.values()):
                    removed.add(nid)

    missing = sorted({m for node in wf.values() for m in PLACEHOLDER.findall(json.dumps(node.get("inputs", {})))})
    if missing:
        raise ComfyError(f"valeurs manquantes pour le gabarit : {', '.join(missing)}")
    return wf
