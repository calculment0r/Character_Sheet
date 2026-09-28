"""Un personnage tiré d'une image : un plein pied, ou une planche de
personnage (Midjourney…).

Cal, 28/09 : « fais-moi le pipe complet pour chaque image jusqu'au modèle
3D en A-pose… attention il y a des persos illustrés et d'autres
photoréalistes, donc attention au LoRA photoréaliste ». L'image donne
tout : l'identité, la tenue et le style. Ce module en tire un personnage
aux règles de la chaîne, puis laisse l'autopilote mener A-pose, vues,
contrôle, mesh et rig.

  1. lecture   le modèle de texte regarde l'image : la fiche (genre, âge,
               origine, carrure, visage), la tenue en anglais, le style
               (photo, illustration 2D, rendu 3D stylisé) décrit en une
               phrase, et si c'est une planche à plusieurs vues ;
  2. plein pied Krea 2, édition d'identité v1.2 (elle met la source au
               cadre de la sortie, `fit`) : le personnage seul, en pied, de
               face, bras le long du corps, sur fond gris uni, 1152 × 2048,
               dans le style de l'image — rien d'autre ne sort de la planche ;
  3. visage    de la même image, en gros plan de face, expression neutre,
               tête et épaules, 1024² ;
  4. validés   par la machine (`validated_by: "import"`) : c'est l'image de
               Cal qui fait autorité, pas un tirage ; l'autopilote part.

Un personnage illustré garde son style partout : `style = "stylized"` et
sa description (`style_desc`) passent dans les prompts de l'A-pose et des
vues ; ni le LoRA des photos (UltraReal, texte → image seulement) ni les
mots de photographie ne s'appliquent aux éditions d'un personnage illustré.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from . import config, memory
from .project import ChainError, Project, now

READ_TASK = """Tu regardes une image de personnage (un plein pied, ou une planche de personnage avec plusieurs
vues et des gros plans). Rends un objet JSON :

- "style" : "photo" si l'image est une photographie réaliste d'une vraie personne ; "3d" si c'est un rendu 3D
  stylisé (film d'animation, figurine, marionnette) ; "illustration" pour un dessin ou une peinture 2D.
- "style_desc" : en ANGLAIS, une phrase qui décrit précisément le rendu pour qu'on le reproduise : technique
  (painterly digital painting, flat cel shading, 3D animated film render, stop-motion puppet…), traits, ombres,
  palette, proportions (réalistes ou exagérées).
- "sheet_layout" : "single" s'il n'y a qu'une figure, "sheet" s'il y a plusieurs vues ou gros plans.
- "face_prompt" : en ANGLAIS, deux ou trois phrases sur le visage et les cheveux seulement : forme du visage, peau,
  yeux, sourcils, nez, bouche, pilosité, coupe et couleur des cheveux, âge apparent, lunettes s'il en porte.
- "outfit_prompt" : en ANGLAIS, trois à cinq phrases sur la tenue de la tête aux pieds : chaque pièce, coupe,
  matière, couleur, logos et motifs, accessoires portés. Rien sur le corps ni le décor.
- "body_prompt" : en ANGLAIS, une phrase sur la silhouette et les proportions (carrure, taille, longueur des
  membres, épaules), exagérées comprises.
- "sheet" : en français, valeurs courtes : gender, age (un nombre), ethnicity, body_type, face_description (une
  phrase), default_outfit_description (une phrase), color_palette (3 à 5 couleurs), personality_traits (trois
  mots que suggère l'attitude).
"""

FIELDS = ("gender", "age", "ethnicity", "body_type", "face_description", "default_outfit_description",
          "color_palette", "personality_traits")


def _schema() -> dict:
    s = {"type": "string"}
    props = {"style": {"type": "string", "enum": ["photo", "3d", "illustration"]}, "style_desc": s,
             "sheet_layout": {"type": "string", "enum": ["single", "sheet"]}, "face_prompt": s, "outfit_prompt": s,
             "body_prompt": s, "sheet": {"type": "object", "properties": {k: s for k in FIELDS}}}
    return {"type": "object", "required": list(props), "properties": props}


def _thumb(path: Path, side: int = 1280) -> str:
    from PIL import Image

    img = Image.open(path).convert("RGB")
    img.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def read(image: Path, timeout: float = 900) -> dict:
    """Ce que le modèle de texte voit dans l'image (voir READ_TASK)."""
    if config.backend("brief") == "stub":
        return {"style": "illustration", "style_desc": "flat stylised illustration", "sheet_layout": "single",
                "face_prompt": "a stylised face", "outfit_prompt": "a dark coat over a white shirt",
                "body_prompt": "tall and lanky", "sheet": {"gender": "homme", "age": "40"}}
    body = {
        "model": config.setting("llm_model", "qwen3-vl-32b-32k"),
        "messages": [{"role": "system", "content": READ_TASK},
                     {"role": "user", "content": "Voici l'image du personnage.", "images": [_thumb(image)]}],
        "format": _schema(), "stream": False, "options": {"temperature": 0.2}, "keep_alive": "5m",
    }
    req = urllib.request.Request(f"{memory.ollama_url()}/api/chat", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            reply = json.loads(res.read())
        data = json.loads(reply["message"]["content"])
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        raise ChainError(f"le modèle de texte n'a pas pu lire l'image : {exc}") from exc
    data["sheet"] = {k: str(v).strip() for k, v in (data.get("sheet") or {}).items() if k in FIELDS and str(v).strip()}
    return data


def _style_clause(info: dict) -> str:
    if info.get("style") == "photo":
        return "A real photograph with natural skin texture and true-to-life colours."
    return (f"Keep exactly the same art style as the source image: {info.get('style_desc', '').strip().rstrip('.')}. "
            "It is not a photograph; keep the same rendering, line work, shading, colours and body proportions.")


def text_fullbody(info: dict) -> str:
    where = ("From this character sheet, take the main character and show only one full-body front view of them. "
             if info.get("sheet_layout") == "sheet" else "")
    return " ".join(filter(None, [
        where + "Show this same character alone, standing upright and facing the camera, arms relaxed along the "
        "sides of the body, the whole body visible from the top of the head to the feet with a small margin, "
        "centred on a plain uniform light grey background.",
        "Keep exactly the same face, hair, body proportions, outfit, accessories and colours.",
        f"Outfit: {info.get('outfit_prompt', '').strip().rstrip('.')}." if info.get("outfit_prompt") else "",
        "No other figure, no strings or wires, no text, no logo sheet layout, no floor pattern.",
        _style_clause(info),
    ]))


def text_face(info: dict) -> str:
    return " ".join(filter(None, [
        "Close-up portrait of this same character's face: head and shoulders, front view, facing the camera "
        "straight on at eye level, calm neutral expression, mouth closed, centred on a plain uniform light grey "
        "background.",
        f"Face: {info.get('face_prompt', '').strip().rstrip('.')}." if info.get("face_prompt") else "",
        # Nommer les lunettes les fait apparaître (« Manteau », 28/09) : on ne
        # garde que ce que montre l'image, sans rien citer qu'elle n'a pas.
        "Keep exactly the same face, hair, skin and features as in the source image, and add nothing that is not "
        "in it. No text.",
        _style_clause(info),
    ]))


def run(p: Project, image: Path, *, costume: str = "tenue-1", seed: int = 7, report=lambda pr, m: None) -> dict:
    """Du fichier `image` au plein pied validé : lecture, plein pied,
    visage, puis verrouillage et validation. L'autopilote est lancé par
    l'appelant (le studio), comme après un plein pied validé par Cal."""
    from . import krea2
    from . import stubs as sketches
    from .chain import identity_seed

    src = p.dir("refs") / f"source{image.suffix.lower() or '.png'}"
    if Path(image).resolve() != src.resolve():
        shutil.copyfile(image, src)
    report(0.02, "lecture de l'image")
    info = read(src)
    print(f"  style : {info.get('style')} — {info.get('style_desc')}")
    print(f"  mise en page : {info.get('sheet_layout')}")
    p.data["style"] = "photoreal" if info.get("style") == "photo" else "stylized"
    p.data["style_desc"] = info.get("style_desc", "")
    p.data["source"] = {"image": p.rel(src), "read": info, "at": now()}
    identity = dict(p.sheet)
    identity.update({k: v for k, v in info["sheet"].items() if v})
    identity.setdefault("character_name", p.data["name"])
    p.data["identity"] = identity
    p.face["prompt_en"] = info.get("face_prompt", "")
    p.face["prompt"] = info["sheet"].get("face_description", "")
    if costume not in p.data["costumes"]:
        costume = p.add_costume(costume, info.get("outfit_prompt", ""), [])
    cos = p.data["costumes"][costume]
    cos["prompt"] = info.get("outfit_prompt", "")
    cos["body_prompt"] = info.get("body_prompt", "")
    p.save()

    # Le plein pied d'abord : le visage se tire ensuite de la même source.
    report(0.15, "le personnage seul, en pied")
    body = p.dir(f"costumes/{costume}/fullbody") / "cand-001.png"
    text = text_fullbody(info)
    krea2.generate(prompt=text, refs=[src], dest=body, seed=seed, size=krea2.FULLBODY, edit="node",
                   report=lambda pr, m: report(0.15 + 0.4 * pr, m),
                   stub=lambda: sketches.mannequin(krea2.FULLBODY, azimuth=0.0, seed=identity_seed(p)))
    body.with_suffix(".json").write_text(json.dumps({"kind": "fullbody", "engine": "krea2", "prompt": text,
                                                     "refs": [p.rel(src)], "seed": seed}, ensure_ascii=False,
                                                    indent=2), encoding="utf-8")
    cos["fullbody"]["candidates"].append({"file": p.rel(body), "seed": seed, "engine": "krea2",
                                          "backend": config.backend("portrait"), "from": "import", "at": now()})
    report(0.6, "le visage, en gros plan")
    face = p.dir("face") / "cand-001.png"
    text_f = text_face(info)
    krea2.generate(prompt=text_f, refs=[src], dest=face, seed=seed, size=krea2.FACE, edit="node",
                   report=lambda pr, m: report(0.6 + 0.35 * pr, m),
                   stub=lambda: sketches.portrait(krea2.FACE, seed=identity_seed(p)))
    face.with_suffix(".json").write_text(json.dumps({"kind": "face", "engine": "krea2", "prompt": text_f,
                                                     "refs": [p.rel(src)], "seed": seed}, ensure_ascii=False,
                                                    indent=2), encoding="utf-8")
    p.face["candidates"].append({"file": p.rel(face), "seed": seed, "engine": "krea2",
                                 "backend": config.backend("portrait"), "from": "import", "at": now()})
    p.face["engine"] = "krea2"
    p.save()
    p.lock_face("1")
    p.validate_fullbody(costume, "1")
    cos = p.data["costumes"][costume]
    cos["fullbody"]["validated_by"] = "import"
    p.save()
    report(1.0, "plein pied et visage tirés de l'image")
    return {"costume": costume, "style": p.data["style"], "style_desc": p.data["style_desc"],
            "layout": info.get("sheet_layout")}
