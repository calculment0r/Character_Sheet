"""Les prompts H3, au format Ref2VA en six sections.

Le format est celui du guide de MiniMax pour le mode « full-reference »
(`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`, livré avec les poids H3) :

  - `subject_definitions` : une ligne par `<Subject n>`, qui cite les
    images d'où il vient (`<Picture n>`, dans l'ordre d'envoi des
    références) et ce que chacune apporte. Une image qui ne sert qu'à
    définir un personnage ou un costume n'a pas de ligne à elle ;
  - `summary` : un paragraphe qui commence par le type de tâche,
    `[reference generation]` ;
  - `retention_analysis` : une ligne par sujet,
    `<Subject n> (appears in [Shot 1]): fully_preserved - …` ;
  - `detailed_description` : une ou deux phrases de style, puis
    `[Shot 1] …` ;
  - `overall_soundscape`, `non_diegetic_music` : neutres, on ne veut
    pas de son.

Les règles dures du §5.3 tiennent : aucun prompt négatif, toute
contrainte s'écrit en prose ; chaque référence porte un rôle nommé ; le
costume se prend tel quel. La pose est l'A-pose du §8.3, identique
partout. Les images sont fixes : cinq frames, caméra immobile.

`js/promptbuilder.js` porte encore l'ancienne forme (« Image 1
defines … ») : c'est ce module qui fait foi pour la chaîne.
"""

from __future__ import annotations

import json

SECTIONS = ("subject_definitions", "summary", "retention_analysis",
            "detailed_description", "overall_soundscape", "non_diegetic_music")

POSE_APOSE = (
    "The subject stands in a relaxed A-pose: arms held about forty-five degrees away from the torso, "
    "palms turned inward toward the thighs, fingers separated and individually visible, legs slightly apart "
    "at hip width, weight evenly distributed on both feet, gaze level with the camera horizon. "
    "There is no twist in the pelvis and no twist in the shoulders, and no perspective emphasis on any limb."
)

TAKE_AS_IS = (
    "every garment piece, strap, buckle, seam, fastening, colour and finish keeps exactly what it has in the "
    "reference images; nothing is redrawn, simplified, tidied, embellished or improved, because this frame "
    "documents an existing design and does not create one"
)

NO_TEXT = "The image carries no text, no caption, no logo and no watermark."

BACKGROUND = ("The background is a plain uniform neutral grey seamless, with no set, no props on the floor and "
              "no cast shadow beyond a soft contact shadow.")

LIGHT = ("Lighting is a single even neutral studio setup, identical on both sides of the subject, with no "
         "coloured rim and no dramatic falloff.")

STILL = ("The camera is locked off and nothing in the frame moves: the subject holds the pose without blinking, "
         "breathing visibly or shifting weight, so every frame of the clip is the same sharp reference image.")

STYLES = {
    "photoreal": ("The target video is a photorealistic studio reference capture of a real person, shot on a "
                  "high-resolution cinema camera with a sharp lens, natural skin texture and true-to-life colour."),
    "stylized": ("The target video is a stylised character reference in a clean studio, with consistent shading, "
                 "clean shapes and true colours, rendered as a production design reference."),
}

AZIMUTHS = {
    "front":        (0.0,   "seen from directly in front, camera azimuth zero degrees"),
    "left":         (90.0,  "seen in pure left profile, camera azimuth ninety degrees"),
    "back":         (180.0, "seen from directly behind, camera azimuth one hundred and eighty degrees"),
    "right":        (270.0, "seen in pure right profile, camera azimuth two hundred and seventy degrees"),
    "threequarter": (45.0,  "seen at a three-quarter angle, camera azimuth forty-five degrees"),
}

SILENT = {"overall_soundscape": "Silence throughout, with no ambience, no foley and no room tone.",
          "non_diegetic_music": "N/A"}


# ── morceaux communs ───────────────────────────────────────────────

def pictures(first: int, count: int) -> str:
    """`<Picture 2>`, `<Picture 2> and <Picture 3>`, `<Picture 2>, <Picture 3> and <Picture 4>`."""
    tags = [f"<Picture {i}>" for i in range(first, first + count)]
    return tags[0] if len(tags) == 1 else f"{', '.join(tags[:-1])} and {tags[-1]}"


def describe_subject(sheet: dict) -> str:
    bits = []
    age = str(sheet.get("age") or "")
    if age and not any(w in age.lower() for w in ("old", "year", "age", "teen", "adult")):
        sheet = {**sheet, "age": f"{age} years"}
    for key, fmt in (("age", "{} old"), ("gender", "{}"), ("ethnicity", "{}"),
                     ("body_type", "{} build"), ("height", "standing {}")):
        if sheet.get(key):
            bits.append(fmt.format(sheet[key]))
    who = ", ".join(bits) if bits else "a person"
    name = sheet.get("character_name") or "the character"
    role = f", working as {sheet['role']}" if sheet.get("role") else ""
    arch = f", archetype: {sheet['archetype']}" if sheet.get("archetype") else ""
    return f"{name}, {who}{role}{arch}"


def describe_outfit(sheet: dict, extra: str = "") -> str:
    parts = []
    if sheet.get("default_outfit_description"):
        parts.append(sheet["default_outfit_description"].rstrip(".") + ".")
    for key, label in (("top_description", "Top"), ("bottom_description", "Bottom"),
                       ("shoes_description", "Shoes"), ("accessories", "Accessories")):
        if sheet.get(key):
            parts.append(f"{label}: {sheet[key]}.")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def _style(style: str) -> str:
    return STYLES.get(style, STYLES["photoreal"])


def _design(sheet: dict, notes: list[str]) -> str:
    return " ".join(filter(None, [
        f"The colour palette of the character is: {sheet['color_palette']}." if sheet.get("color_palette") else "",
        f"Props present: {sheet['props']}." if sheet.get("props") else "",
        f"Design notes to respect: {'; '.join(notes)}." if notes else "",
    ]))


def _cast(sheet: dict, *, garments: int, body_ref: bool, sheet_ref: bool, outfit: str) -> tuple[list[str], list[str]]:
    """Les deux sujets des étages habillés, dans l'ordre d'envoi des
    références : `<Picture 1>` le visage verrouillé, puis le plein pied
    validé et la planche s'il y en a, puis les pièces de vêtement.

    Rend les lignes de `subject_definitions` et de `retention_analysis`.
    Le costume n'est un sujet que s'il a une image : sans elle, il
    n'existe qu'en prose dans la description."""
    body_src = ""
    if body_ref:
        body_src = ", and whose body proportions come from <Picture 2>" + (" and <Picture 3>" if sheet_ref else "")
    defs = [f"<Subject 1> is {describe_subject(sheet)}, whose face, hair, age and identity come from "
            f"<Picture 1>{body_src}."]
    keep = ["<Subject 1> (appears in [Shot 1]): fully_preserved - the face, bone structure, eyes, nose, mouth, "
            "skin tone, hair and age are retained exactly" + (", and so are the body proportions" if body_ref else "")
            + "."]
    first_garment = 2 + int(body_ref) + int(sheet_ref)
    sources = []
    if body_ref:
        sources.append("as worn in <Picture 2>")
    if sheet_ref:
        sources.append("with its design from every side in <Picture 3>")
    if garments:
        sources.append(f"made of the garment pieces in {pictures(first_garment, garments)}")
    if sources:
        defs.append(" ".join(filter(None, [f"<Subject 2> is the costume {', '.join(sources)}.", outfit])))
        keep.append(f"<Subject 2> (appears in [Shot 1]): fully_preserved - {TAKE_AS_IS}.")
    return defs, keep


def _wears(has_costume: bool) -> str:
    return "<Subject 1> wearing <Subject 2>" if has_costume else "<Subject 1>"


def _sections(defs: list[str], summary: str, keep: list[str], style: str, shot: str) -> dict:
    return {
        "subject_definitions": "\n".join(defs) if defs else "N/A",
        "summary": summary,
        "retention_analysis": "\n".join(keep) if keep else "N/A",
        "detailed_description": f"{_style(style)}\n[Shot 1] {shot}",
        **SILENT,
    }


# ── les étages ─────────────────────────────────────────────────────

def face(sheet: dict, notes: list[str], *, has_source: bool, extra: str = "", style: str = "photoreal") -> dict:
    """Le portrait neutre du §3 : lumière égale, bouche fermée, regard
    caméra, fond uni. Avec une photo, c'est une passe de normalisation."""
    if has_source:
        defs = [f"<Subject 1> is the person in <Picture 1>, {describe_subject(sheet)}."]
        keep = ["<Subject 1> (appears in [Shot 1]): fully_preserved - the face, bone structure, eyes, nose, mouth, "
                "skin tone, hair and age of the person are retained exactly; only framing, lighting and expression "
                "are normalised."]
        who = "<Subject 1>"
        summary = ("[reference generation] A single static identity portrait of <Subject 1>, head and shoulders, "
                   "facing the camera straight on at eye level, on a plain neutral seamless background, with the "
                   "framing, lighting and expression normalised.")
    else:
        defs, keep = [], []
        who = describe_subject(sheet)
        summary = ("A single static identity portrait of the character, head and shoulders, facing the camera "
                   "straight on at eye level, on a plain neutral seamless background.")
    shot = " ".join(filter(None, [
        f"A locked-off head-and-shoulders portrait of {who}, centred, facing the lens straight on at eye level.",
        "The mouth is closed, the expression is neutral and relaxed, the eyes look straight into the lens.",
        "The hair is arranged as described and does not cover the eyes.",
        f"Design notes to respect: {'; '.join(notes)}." if notes else "",
        extra,
        "Even, soft, neutral studio light from the front, identical on both sides of the face, with no hard shadow "
        "and no coloured rim.",
        STILL, BACKGROUND, NO_TEXT,
    ]))
    return _sections(defs, summary, keep, style, shot)


def fullbody(sheet: dict, notes: list[str], *, garments: int, costume_prompt: str = "",
             style: str = "photoreal") -> dict:
    """Le plein pied de référence d'un costume, le visage verrouillé
    servant de référence d'identité (§4)."""
    outfit = describe_outfit(sheet, costume_prompt)
    defs, keep = _cast(sheet, garments=garments, body_ref=False, sheet_ref=False, outfit=outfit)
    dressed = garments > 0
    shot = " ".join(filter(None, [
        f"A locked-off full-body shot of {_wears(dressed)}, seen from directly in front, the whole figure from "
        "head to feet inside the frame with an even margin on every side.",
        "" if dressed else (f"The costume: {outfit}" if outfit else ""),
        POSE_APOSE, _design(sheet, notes), LIGHT, STILL, BACKGROUND, NO_TEXT,
    ]))
    summary = (f"[reference generation] A single static full-body reference frame of {_wears(dressed)}, seen from "
               "directly in front, standing in a relaxed A-pose on a plain neutral grey seamless.")
    return _sections(defs, summary, keep, style, shot)


def plate(sheet: dict, notes: list[str], *, garments: int, mask_face: bool, costume_prompt: str = "",
          style: str = "photoreal") -> dict:
    """La planche du §5.4 corrigé : cinq pleins pieds, puis les gros plans."""
    defs, keep = _cast(sheet, garments=garments, body_ref=True, sheet_ref=False,
                       outfit=describe_outfit(sheet, costume_prompt))
    head = ("In the five full-body views the head is covered by a plain neutral grey disc; there the head serves "
            "only silhouette and proportion, and the face appears only in the close-ups." if mask_face else
            "In the full-body views the head serves silhouette and proportion; the close-ups carry the face.")
    shot = " ".join(filter(None, [
        "A locked-off frontal shot of a single character model sheet laid out as a clean studio grid. The top row "
        "holds five full-body views of <Subject 1> wearing <Subject 2> at identical scale and identical margins: "
        "front at zero degrees, left profile at ninety, back at one hundred and eighty, right profile at two "
        "hundred and seventy, and a three-quarter view at forty-five. The bottom row holds face close-ups of "
        "<Subject 1>: neutral front, pure profile, and three-quarter.",
        head,
        "The scale of the subject is constant from panel to panel, and so is the margin around the silhouette.",
        POSE_APOSE, _design(sheet, notes), LIGHT, STILL, BACKGROUND, NO_TEXT,
    ]))
    summary = ("[reference generation] A single static character model sheet of <Subject 1> wearing <Subject 2>: "
               "five full-body views in a row, then face close-ups, on a plain neutral grey seamless.")
    return _sections(defs, summary, keep, style, shot)


def view(sheet: dict, notes: list[str], *, name: str, costume_prompt: str = "", style: str = "photoreal") -> dict:
    """Une vue orthogonale plein cadre (§6.1) : visage, plein pied et
    planche validée en références, l'angle exact et la pose en prose."""
    _, prose = AZIMUTHS[name]
    defs, keep = _cast(sheet, garments=0, body_ref=True, sheet_ref=True,
                       outfit=describe_outfit(sheet, costume_prompt))
    shot = " ".join(filter(None, [
        f"A locked-off full-body orthographic reference frame of <Subject 1> wearing <Subject 2>, {prose}. The "
        "subject fills the frame from head to feet with an even margin on every side.",
        "The camera is level with the chest, with a long focal length and no perspective distortion, so the view "
        "reads as orthographic.",
        POSE_APOSE, f"Design notes to respect: {'; '.join(notes)}." if notes else "",
        LIGHT, STILL, BACKGROUND, NO_TEXT,
    ]))
    summary = (f"[reference generation] A single static full-body orthographic reference frame of <Subject 1> "
               f"wearing <Subject 2>, {prose}, on a plain neutral grey seamless.")
    return _sections(defs, summary, keep, style, shot)


def orbit(sheet: dict, notes: list[str], *, costume_prompt: str = "", style: str = "photoreal") -> dict:
    """L'alternative du §6.1 : un seul plan, caméra en orbite complète
    autour d'un sujet immobile, dont on extrait les azimuts voulus."""
    defs, keep = _cast(sheet, garments=0, body_ref=True, sheet_ref=True,
                       outfit=describe_outfit(sheet, costume_prompt))
    shot = " ".join(filter(None, [
        "A single continuous shot: the camera orbits a full three hundred and sixty degrees around <Subject 1> "
        "wearing <Subject 2>, at constant speed and constant distance, level with the chest, starting from the "
        "front and turning toward the subject's left side. The whole figure stays inside the frame.",
        "<Subject 1> does not move at all during the shot; only the camera moves.",
        POSE_APOSE, LIGHT, BACKGROUND, NO_TEXT,
    ]))
    summary = ("[reference generation] A single continuous orbit of the camera around <Subject 1> wearing "
               "<Subject 2>, who stands motionless in a relaxed A-pose on a plain neutral grey seamless.")
    return _sections(defs, summary, keep, style, shot)


def to_text(sections: dict) -> str:
    """À plat, sections dans l'ordre, pour l'affichage et pour H3."""
    return "\n\n".join(f"{k}:\n{sections.get(k, '')}" for k in SECTIONS)


def to_json(sections: dict) -> str:
    return json.dumps({k: sections.get(k, "") for k in SECTIONS}, ensure_ascii=False, indent=2)
