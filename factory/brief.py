"""Lire un brief visuel.

On décrit ce qu'on veut voir, en français, en une phrase ou dix, avec
des images si on en a. Le modèle de texte de la machine (Ollama, sortie
structurée) en tire deux choses :

  - le prompt d'image, en anglais, limité à ce que l'étage montre : le
    visage et les cheveux pour le portrait, les vêtements pour un
    costume. Le cadrage, la lumière, le fond et la tête nue restent
    écrits dans le code (`portrait.py`, `prompts.py`) : le modèle ne
    fait que décrire ;
  - ce qu'il en comprend pour la fiche d'identité, en français, rangé
    dans les champs de la console (`js/schema.js`).

La fiche se remplit ainsi à partir du visuel, sans questionnaire.

Il lit aussi les retouches du visage verrouillé (`read_edit`) : un look
(« une fine cicatrice sur la pommette gauche ») ou une expression, dits
en français, deviennent une consigne anglaise précise — le côté du corps
nommé, l'expression écrite en mouvements du visage — et un nom court.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import config, memory
from .project import ChainError

FACE_FIELDS = ("gender", "age", "ethnicity", "body_type", "face_description")
COSTUME_FIELDS = ("default_outfit_description", "top_description", "bottom_description", "shoes_description",
                  "accessories", "color_palette")

FACE_TASK = """Tu prépares le portrait d'identité d'un personnage : un visage neutre, de face, tête nue, fond uni.
À partir du brief (et des images s'il y en a), rends un objet JSON :

- "prompt" : en ANGLAIS, une à trois phrases qui décrivent UNIQUEMENT le visage et les cheveux, de façon
  concrète et visible : forme du visage, peau (teint, grain, marques, cicatrices), yeux, sourcils, nez,
  bouche, pilosité, coupe et couleur des cheveux, âge apparent. Rien d'autre : ni vêtement, ni couvre-chef,
  ni capuche, ni lunettes, ni bijou, ni accessoire, ni objet, ni métier, ni décor, ni expression jouée. Si le
  brief parle d'un casque, d'une casquette ou d'une capuche, ignore-les : ils iront au costume. Si un détail
  du visage manque, choisis-en un plausible et cohérent avec le reste.
- "variants" : quatre autres descriptions du même genre, en anglais, chacune une proposition DIFFÉRENTE et
  plausible du personnage, toutes fidèles au brief : varie la forme du visage, la coupe et la texture des
  cheveux, les traits, la pilosité, les marques — ce que le brief laisse libre. Elles servent à explorer :
  quatre visages qui ne se ressemblent pas, pas quatre fois le même.
- "sheet" : ce que le brief dit de la personne, en français, valeurs courtes :
  gender (femme / homme / non-binaire…), age (un nombre d'années), ethnicity, body_type,
  face_description (visage et cheveux, en une phrase française). Laisse "" ce que le brief ne permet pas de
  dire, sauf face_description.
"""

COSTUME_TASK = """Tu prépares le costume d'un personnage, porté en pied sur un fond uni.
À partir du brief (et des images de vêtements s'il y en a), rends un objet JSON :

- "prompt" : en ANGLAIS, deux à cinq phrases qui décrivent UNIQUEMENT la tenue, de la tête aux pieds, de
  façon concrète : chaque pièce, coupe, matière, couleur, état (neuf, usé, déchiré), fermetures, et les
  accessoires portés (couvre-chef, lunettes, bijoux, sac). Rien sur le visage ni le corps, pas de décor.
  Écris « a cap » pour une casquette, « a helmet » seulement s'il s'agit vraiment d'un casque.
- "sheet" : en français, valeurs courtes : default_outfit_description (la tenue en une phrase),
  top_description, bottom_description, shoes_description, accessories, color_palette (3 à 5 couleurs).
"""


# Les retouches du visage verrouillé (`looks.py`, `presentation.add_expression`) :
# le modèle n'écrit que le changement ; ce qui ne bouge pas reste dans le code.
LOOK_TASK = """Tu prépares une retouche de la photo d'identité d'un personnage : un portrait de face, qui regarde
l'objectif. La retouche change un détail de son apparence (maquillage, cicatrice, grain de beauté, coiffure,
pilosité, lunettes, bijou…) sans changer la personne. À partir de la consigne, rends un objet JSON :

- "prompt" : en ANGLAIS, une phrase courte à l'impératif (« Add… », « Give the person… »), trente mots au plus,
  qui dit exactement quoi changer et où : matière, couleur, taille, intensité, place sur le visage ou la tête.
  N'ajoute rien que la consigne ne dit pas : ni longueur, ni trajet, ni détail inventé ; « fine » reste fin et
  petit, « discret » reste à peine visible. Un côté est toujours celui du corps de la personne : écris par
  exemple « on the person's own left cheekbone (the right side of the photo) ». Ne décris que le changement :
  ni le cadrage, ni la pose, ni la lumière, ni le fond.
- "name" : en français, deux à quatre mots en bas de casse qui nomment ce look (par exemple « maquillage de
  soirée », « cicatrice à la pommette »).
"""

EXPRESSION_TASK = """Tu prépares une expression du visage d'un personnage, faite en retouchant sa photo de face.
Une expression nommée par son émotion (« joie », « colère ») sort jouée, forcée, comme au théâtre : on veut un
visage vivant, naturel, saisi sur le vif. À partir de la consigne, rends un objet JSON :

- "prompt" : en ANGLAIS, une ou deux phrases courtes, quarante mots au plus, qui décrivent le mouvement du
  visage, en douceur : ce que font les sourcils, les paupières et le regard, les joues, la bouche et les lèvres,
  la mâchoire. Des mouvements légers et vrais, comme sur une photo prise sans que la personne pose : jamais une
  grimace, jamais un mouvement poussé à fond. Des faits, sans récit ni commentaire (pas de « revealing… »,
  « as if… », « enhancing… ») ; « the person's », jamais « her » ou « his ». N'écris pas le nom de l'émotion. La
  tête ne bouge pas. Un côté est celui du visage de la personne (« the person's own left eyebrow »). Ni cadrage,
  ni lumière, ni fond.
- "name" : en français, un à trois mots en bas de casse qui nomment l'expression (par exemple « sourire en coin »,
  « regard amusé »).
"""

_FRENCH_WORDS = {"le", "la", "les", "du", "des", "un", "une", "sur", "avec", "et", "au", "aux", "sous", "dans",
                 "gauche", "droite", "joue", "yeux", "oeil", "œil", "cheveux", "plus", "très", "peu", "pas", "sans",
                 "son", "sa", "ses", "qui", "comme", "fine", "léger", "légère", "sourire", "regard", "bouche"}
_TRAILING = {"sur", "la", "le", "les", "de", "du", "des", "à", "au", "aux", "avec", "et", "en", "un", "une", "d", "l",
             "on", "the", "of", "with", "and", "a", "an", "to", "in"}


def french(text: str) -> bool:
    """Une consigne en français : un accent ou un mot courant suffit."""
    t = text.lower()
    return bool(re.search(r"[àâäçéèêëîïôöùûüÿœæ]", t)) or bool(_FRENCH_WORDS & set(re.findall(r"[a-zœ]+", t)))


def short_name(text: str, words: int = 4) -> str:
    """Un nom de secours, tiré de la consigne : sans article, quelques mots."""
    t = re.sub(r"^(?:(?:du|de la|des|une|un|les|le|la|an|a|the)\s+|(?:de l|d|l)['’]\s*)", "", text.strip(),
               flags=re.I)
    kept = [w.strip(",.;:") for w in t.split()[:words]]
    while len(kept) > 1 and kept[-1].lower() in _TRAILING:
        kept.pop()
    return " ".join(kept)[:40] or text.strip()[:40]


def _chat(task: str, user: str, schema: dict, *, images=(), timeout: float = 600) -> dict:
    """Un appel au modèle de texte d'Ollama, sortie JSON contrainte par `schema`."""
    body = {
        "model": config.setting("llm_model", "qwen3-vl-32b-32k"),
        "messages": [{"role": "system", "content": task},
                     {"role": "user", "content": user, **({"images": _images(images)} if images else {})}],
        "format": schema, "stream": False, "options": {"temperature": 0.6}, "keep_alive": "15m",
    }
    req = urllib.request.Request(f"{memory.ollama_url()}/api/chat", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            reply = json.loads(res.read())
    except (urllib.error.URLError, OSError) as exc:
        raise ChainError(f"le modèle de texte ne répond pas ({memory.ollama_url()}) : {exc}") from exc
    try:
        data = json.loads(reply["message"]["content"])
    except (KeyError, ValueError) as exc:
        raise ChainError(f"réponse illisible du modèle de texte : {str(reply)[:300]}") from exc
    return data if isinstance(data, dict) else {}


def read_edit(task: str, text: str, *, sheet: dict, timeout: float = 300) -> dict:
    """Une consigne de retouche (`LOOK_TASK`, `EXPRESSION_TASK`) devenue une
    consigne anglaise précise, et un nom court. En factice, elle passe
    telle quelle."""
    text = text.strip()
    if config.backend("brief") == "stub":
        return {"prompt": text, "name": short_name(text)}
    context = {k: sheet[k] for k in ("gender", "age", "face_description")
               if isinstance(sheet.get(k), str) and sheet[k].strip()}
    user = f"Consigne :\n{text}\n\nLa personne :\n{json.dumps(context, ensure_ascii=False, indent=1)}"
    schema = {"type": "object", "required": ["prompt", "name"],
              "properties": {"prompt": {"type": "string"}, "name": {"type": "string"}}}
    data = _chat(task, user, schema, timeout=timeout)
    return {"prompt": str(data.get("prompt") or "").strip() or text,
            "name": str(data.get("name") or "").strip()[:40] or short_name(text)}


def _schema(fields: tuple[str, ...], variants: bool = False) -> dict:
    props = {"prompt": {"type": "string"},
             "sheet": {"type": "object", "properties": {k: {"type": "string"} for k in fields}}}
    if variants:
        props["variants"] = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "required": list(props), "properties": props}


def _images(paths) -> list[str]:
    out = []
    for p in paths or []:
        try:
            out.append(base64.b64encode(Path(p).read_bytes()).decode())
        except OSError:
            continue
    return out


def ask(task: str, fields: tuple[str, ...], brief: str, *, sheet: dict, images=(), timeout: float = 600) -> dict:
    """Un appel au modèle de texte, sortie JSON contrainte par un schéma.
    En factice (`FACTORY_BRIEF=stub`), le brief passe tel quel."""
    variants = "face_description" in fields
    if config.backend("brief") == "stub":
        key = "face_description" if variants else "default_outfit_description"
        return {"prompt": brief.strip(), "sheet": {key: brief.strip()} if brief.strip() else {},
                "variants": [f"{brief.strip()} (variation {i})" for i in range(1, 5)] if variants else []}
    context = {k: v for k, v in sheet.items() if isinstance(v, str) and v.strip()}
    user = (f"Brief :\n{brief.strip() or '(vide : invente une proposition cohérente avec la fiche)'}\n\n"
            f"Fiche actuelle (à respecter, sauf si le brief la contredit) :\n"
            f"{json.dumps(context, ensure_ascii=False, indent=1)}")
    data = _chat(task, user, _schema(fields, variants), images=images, timeout=timeout)
    sheet_out = {k: str(v).strip() for k, v in (data.get("sheet") or {}).items() if k in fields and str(v).strip()}
    # L'âge est un nombre ou rien : le modèle a déjà rendu « use » pour « 20 ans ».
    if "age" in sheet_out:
        digits = re.search(r"\d{1,3}", sheet_out["age"])
        if digits:
            sheet_out["age"] = digits.group(0)
        else:
            del sheet_out["age"]
    alts = [str(v).strip() for v in data.get("variants") or [] if str(v).strip()] if variants else []
    return {"prompt": str(data.get("prompt") or "").strip(), "sheet": sheet_out, "variants": alts[:6]}


def read_face(brief: str, *, sheet: dict, images=()) -> dict:
    return ask(FACE_TASK, FACE_FIELDS, brief, sheet=sheet, images=images)


def read_costume(brief: str, *, sheet: dict, images=()) -> dict:
    return ask(COSTUME_TASK, COSTUME_FIELDS, brief, sheet=sheet, images=images)
