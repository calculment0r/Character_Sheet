"""Les prompts H3, au format Ref2VA en six sections.

Même méthode que `js/promptbuilder.js`, portée ici pour que la chaîne
tourne sans navigateur. Les règles dures du §5.3 :

  - aucun prompt négatif : toute contrainte s'écrit en prose, dans le
    positif ;
  - chaque référence porte un rôle explicite et nommé, sans quoi le
    modèle pioche dans toutes les images ;
  - un bloc « prendre tel quel » ;
  - les deux sections sonores restent neutres : on ne veut pas de son.

La pose est l'A-pose du §8.3, écrite en prose et identique partout.
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
    "Every garment piece, strap, buckle, seam, fastening, colour and finish keeps exactly what it has in the "
    "reference images. Nothing is redrawn, simplified, tidied, embellished or improved. This plate documents an "
    "existing design; it does not create one."
)

NO_TEXT = "The image carries no text, no caption, no logo and no watermark."

BACKGROUND = ("The background is a plain uniform neutral grey seamless, with no set, no props on the floor and "
              "no cast shadow beyond a soft contact shadow.")

LIGHT = ("Lighting is a single even neutral studio setup, identical across every panel, with no coloured rim "
         "and no dramatic falloff.")

AZIMUTHS = {
    "front":        (0.0,   "seen from directly in front, camera azimuth zero degrees"),
    "left":         (90.0,  "seen in pure left profile, camera azimuth ninety degrees"),
    "back":         (180.0, "seen from directly behind, camera azimuth one hundred and eighty degrees"),
    "right":        (270.0, "seen in pure right profile, camera azimuth two hundred and seventy degrees"),
    "threequarter": (45.0,  "seen at a three-quarter angle, camera azimuth forty-five degrees"),
}

# Les rôles des références, dans l'ordre où elles partent.
ROLE_FACE = "the face, hair, age and identity of the character"
ROLE_FULLBODY = "the costume as worn, and the body proportions of the character"
ROLE_SHEET = "the validated character sheet: design, proportions and colours"
ROLE_GARMENT = "garment reference {n}"
ROLE_SOURCE = "the person whose face is to be normalised"


def describe_subject(sheet: dict) -> str:
    bits = []
    for key, fmt in (("age", "{} old"), ("gender", "{}"), ("ethnicity", "{}"),
                     ("body_type", "{} build"), ("height", "standing {}")):
        if sheet.get(key):
            bits.append(fmt.format(sheet[key]))
    who = ", ".join(bits) if bits else "a person"
    name = sheet.get("character_name") or "the character"
    role = f", working as {sheet['role']}" if sheet.get("role") else ""
    arch = f", archetype: {sheet['archetype']}" if sheet.get("archetype") else ""
    return f"{name} is {who}{role}{arch}."


def describe_outfit(sheet: dict, extra: str = "") -> str:
    parts = []
    if sheet.get("default_outfit_description"):
        parts.append(sheet["default_outfit_description"])
    for key, label in (("top_description", "Top"), ("bottom_description", "Bottom"),
                       ("shoes_description", "Shoes"), ("accessories", "Accessories")):
        if sheet.get(key):
            parts.append(f"{label}: {sheet[key]}.")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def _definitions(refs: list[str], sheet: dict, outfit: str) -> str:
    named = (" ".join(f"Image {i + 1} defines {role}." for i, role in enumerate(refs))
             if refs else "No reference image is supplied; the subject is defined by the written description alone.")
    return " ".join(x for x in (named, describe_subject(sheet), outfit) if x)


def _silent(sections: dict) -> dict:
    sections["overall_soundscape"] = "Silent. No ambience, no foley, no room tone."
    sections["non_diegetic_music"] = "None."
    return sections


def face(sheet: dict, notes: list[str], *, has_source: bool, extra: str = "") -> dict:
    """Le portrait neutre du §3 : lumière égale, bouche fermée, regard
    caméra, fond uni. Avec une photo, c'est une passe de normalisation."""
    refs = [ROLE_SOURCE] if has_source else []
    summary = ("A single neutral identity portrait, head and shoulders, facing the camera straight on at eye "
               "level, on a plain neutral seamless background.")
    retention = " ".join(filter(None, [
        "The identity of the face is defined exclusively by Image 1; framing, lighting and expression are "
        "normalised, the identity is not changed." if has_source else "",
        "Even, soft, neutral studio light from the front, identical on both sides of the face, with no hard "
        "shadow and no coloured rim.",
        "The mouth is closed, the expression is neutral and relaxed, the eyes look straight into the lens.",
    ]))
    detailed = " ".join(filter(None, [
        f"Design notes to respect: {'; '.join(notes)}." if notes else "",
        extra,
        "The hair is arranged as described and does not cover the eyes.",
        BACKGROUND, NO_TEXT,
    ]))
    return _silent({
        "subject_definitions": _definitions(refs, sheet, ""),
        "summary": summary,
        "retention_analysis": retention,
        "detailed_description": detailed,
    })


def fullbody(sheet: dict, notes: list[str], *, garments: int, costume_prompt: str = "") -> dict:
    """Le plein pied de référence d'un costume, le visage verrouillé
    servant de référence d'identité (§4)."""
    refs = [ROLE_FACE] + [ROLE_GARMENT.format(n=i + 1) for i in range(garments)]
    return _silent({
        "subject_definitions": _definitions(refs, sheet, describe_outfit(sheet, costume_prompt)),
        "summary": ("A single full-body reference frame of the subject seen from directly in front, the whole "
                    "figure from head to feet inside the frame with an even margin on every side."),
        "retention_analysis": " ".join(filter(None, [
            "The identity of the face is defined exclusively by Image 1.",
            TAKE_AS_IS if garments else "",
            LIGHT,
        ])),
        "detailed_description": " ".join(filter(None, [
            POSE_APOSE,
            f"The colour palette of the character is: {sheet['color_palette']}." if sheet.get("color_palette") else "",
            f"Props present: {sheet['props']}." if sheet.get("props") else "",
            f"Design notes to respect: {'; '.join(notes)}." if notes else "",
            BACKGROUND, NO_TEXT,
        ])),
    })


def plate(sheet: dict, notes: list[str], *, garments: int, mask_face: bool, costume_prompt: str = "") -> dict:
    """La planche du §5.4 corrigé : cinq pleins pieds, puis les gros plans."""
    refs = [ROLE_FACE, ROLE_FULLBODY] + [ROLE_GARMENT.format(n=i + 1) for i in range(garments)]
    return _silent({
        "subject_definitions": _definitions(refs, sheet, describe_outfit(sheet, costume_prompt)),
        "summary": (
            "A single character model sheet laid out as a clean studio grid. The top row holds five full-body "
            "views at identical scale and identical margins — front at zero degrees, left profile at ninety, back "
            "at one hundred and eighty, right profile at two hundred and seventy, and a three-quarter view at "
            "forty-five. The bottom row holds face close-ups: neutral front, pure profile, and three-quarter."),
        "retention_analysis": " ".join([
            "The identity of the face is defined exclusively by Image 1.",
            ("In the full-body panels the head is covered by a plain neutral disc; the head there serves only "
             "silhouette and proportion and is not an identity reference.") if mask_face else
            ("In the full-body panels the head serves only silhouette and proportion and is not an identity "
             "reference."),
            TAKE_AS_IS, LIGHT,
            "The scale of the subject is constant from panel to panel, and the margin around the silhouette is "
            "constant too.",
        ]),
        "detailed_description": " ".join(filter(None, [
            POSE_APOSE,
            f"The colour palette of the character is: {sheet['color_palette']}." if sheet.get("color_palette") else "",
            f"Props present: {sheet['props']}." if sheet.get("props") else "",
            f"Design notes to respect: {'; '.join(notes)}." if notes else "",
            BACKGROUND, NO_TEXT,
        ])),
    })


def view(sheet: dict, notes: list[str], *, name: str, costume_prompt: str = "") -> dict:
    """Une vue orthogonale plein cadre (§6.1) : visage, plein pied et
    planche validée en références, l'angle exact et la pose en prose."""
    _, prose = AZIMUTHS[name]
    refs = [ROLE_FACE, ROLE_FULLBODY, ROLE_SHEET]
    return _silent({
        "subject_definitions": _definitions(refs, sheet, describe_outfit(sheet, costume_prompt)),
        "summary": (f"A single full-body orthographic reference frame of the subject, {prose}. The subject fills "
                    "the frame with an even margin on every side, standing on a plain neutral seamless background."),
        "retention_analysis": " ".join([
            "The identity of the face is defined exclusively by Image 1. The costume is exactly the one of "
            "Image 2 and Image 3.",
            TAKE_AS_IS, LIGHT,
            "The camera is level with the chest, with a long focal length and no perspective distortion, so the "
            "view reads as orthographic.",
        ]),
        "detailed_description": " ".join(filter(None, [
            POSE_APOSE,
            f"Design notes to respect: {'; '.join(notes)}." if notes else "",
            BACKGROUND, NO_TEXT,
        ])),
    })


def orbit(sheet: dict, notes: list[str], *, costume_prompt: str = "") -> dict:
    """L'alternative du §6.1 : un seul plan, caméra en orbite complète
    autour d'un sujet immobile, dont on extrait les azimuts voulus."""
    refs = [ROLE_FACE, ROLE_FULLBODY, ROLE_SHEET]
    return _silent({
        "subject_definitions": _definitions(refs, sheet, describe_outfit(sheet, costume_prompt)),
        "summary": ("A single continuous shot: the camera orbits a full three hundred and sixty degrees around the "
                    "motionless subject at constant speed and constant distance, level with the chest, starting "
                    "from the front and turning toward the subject's left side."),
        "retention_analysis": " ".join([
            "The subject does not move at all during the shot; only the camera moves.",
            "The identity of the face is defined exclusively by Image 1.",
            TAKE_AS_IS, LIGHT,
        ]),
        "detailed_description": " ".join(filter(None, [POSE_APOSE, BACKGROUND, NO_TEXT])),
    })


def to_text(sections: dict) -> str:
    """À plat, sections dans l'ordre, pour l'affichage et pour H3."""
    return "\n\n".join(f"{k}:\n{sections.get(k, '')}" for k in SECTIONS)


def to_json(sections: dict) -> str:
    return json.dumps({k: sections.get(k, "") for k in SECTIONS}, ensure_ascii=False, indent=2)
