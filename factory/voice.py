"""L'étage Voix : une voix conçue, verrouillée, puis dirigée.

Parallèle au visage, et construit comme lui :

  1. **Audition** (`design`) : une description de la voix, écrite par le
     modèle de texte depuis la fiche (âge, genre, origine, carrure,
     personnalité, façon de parler) si on ne la donne pas ; Qwen3-TTS
     VoiceDesign en tire N candidates sur le même paragraphe français
     d'essai (`TEST_TEXT`), une graine chacune.
  2. **Verrouillage** (`lock`) : la candidate choisie devient
     `voice/ref_neutral.wav` + `ref_neutral.txt` + `card.yaml` +
     `consentement.txt` — le format d'une voix du Voice Lab de NIRVALAB.
     Une seule fois, comme le visage ; `unlock` la libère (la référence
     part aux archives), c'est un geste explicite.
  3. **Tenue** : tout ce qui doit sonner comme lui se clone depuis cette
     référence (Qwen3-TTS Base) — la conversation en premier.
  4. **Jeu** (`line`) : une réplique + une direction d'acteur. Sans
     direction, le modèle de texte écrit l'état de jeu en français (et sa
     consigne en anglais pour le moteur). Plusieurs prises par
     VoiceDesign, description de la voix + consigne ; chacune mesurée
     contre la référence (similarité de timbre, ECAPA) pour écarter
     celles qui ne sont plus sa voix. On garde la meilleure (`keep`).

Tout vit sous `projects/<perso>/voice/` et dans `project.json["voice"]`.
"""

from __future__ import annotations

import json
import shutil

from . import config, voice_chat, voice_engine
from .project import ChainError, Project, now
from .project import voice as voice_default

# Le paragraphe d'audition : court (une douzaine de secondes), français,
# une question, une hésitation, une exclamation, des voyelles nasales et
# des liaisons — de quoi juger un timbre et une diction, et une
# référence assez longue pour cloner.
TEST_TEXT = ("Bon, écoute-moi bien, parce que je ne vais pas le répéter. Ce matin, il faisait un froid de "
             "canard, le café était trop chaud, et personne n'avait encore ouvert la porte. Tu crois vraiment "
             "que ça va marcher ? Moi... j'en doute un peu. Mais on verra bien !")

CONSENT = ("Voix synthétique conçue par Qwen3-TTS VoiceDesign à partir d'une description écrite : "
           "aucun humain enregistré, pas de consentement requis.\n")


def state(p: Project) -> dict:
    """L'état Voix d'un personnage (défauts posés à l'ouverture, `project.voice`)."""
    return voice_default(p.data)


def locked_ref(p: Project) -> tuple[bytes, str] | None:
    v = state(p)
    if not v.get("locked"):
        return None
    path = p.path(v["locked"])
    if not path.is_file():
        return None
    return path.read_bytes(), v.get("locked_text") or ""


def require_locked(p: Project) -> tuple[bytes, str]:
    ref = locked_ref(p)
    if ref is None:
        raise ChainError("la voix n'est pas verrouillée — auditionne (voice_design / `./usine voix`) puis verrouille "
                         "une candidate (voice_lock / `./usine voix-ok`)")
    return ref


# ── la description de la voix ──────────────────────────────────────

VOICE_TASK = """Tu conçois la voix d'un personnage pour un moteur de synthèse vocale qui crée une voix à partir d'une
description (Qwen3-TTS VoiceDesign). À partir de la fiche, rends un objet JSON :

- "description" : en ANGLAIS, 30 à 60 mots, la voix telle qu'on l'entend, dans une lecture calme et neutre (cette
  voix servira de référence pour tout le reste) : genre et âge apparent, hauteur (low / mid / high), timbre et
  texture (warm, husky, raspy, breathy, bright, nasal, smooth...), débit, énergie, articulation, accent (précise
  « native French speaker » et l'accent régional ou étranger s'il y en a un), et un trait de caractère qui
  s'entend. Pas d'émotion forte, pas de jeu : c'est la voix au repos.
- "resume" : la même voix en une phrase française courte, pour la fiche.
- "registre" : "grave", "medium" ou "aigu".
"""

PLAY_TASK = """Tu es directeur d'acteurs. Le personnage va dire une réplique ; tu écris l'état de jeu qui la fait
sonner juste. À partir de la fiche, de sa voix et de la réplique (et de la situation si elle est donnée), rends un
objet JSON :

- "etat" : en français, une à deux phrases : la situation, à qui il parle, ce qu'il veut, l'émotion et son
  intensité, l'énergie, le débit, les silences.
- "instruct" : la même direction en ANGLAIS, pour le moteur de voix : une phrase concrète sur l'émotion,
  l'intensité, le débit, le volume, la qualité de voix (whispered, strained, trembling, laughing...). Ne décris
  pas le timbre : il est déjà fixé.
"""


def _schema(*keys: str) -> dict:
    return {"type": "object", "required": list(keys), "properties": {k: {"type": "string"} for k in keys}}


def draft_description(p: Project) -> dict:
    """La voix décrite depuis la fiche : {description, resume, registre}."""
    sheet = p.sheet
    if voice_chat.llm_is_stub():
        bits = [sheet.get(k, "") for k in ("gender", "age", "ethnicity", "speech_style") if sheet.get(k)]
        fr = "voix de " + (", ".join(bits) or p.data["name"])
        return {"description": f"Calm neutral voice of {p.data['name']}, native French speaker ({fr}).",
                "resume": fr, "registre": "medium"}
    user = f"Personnage : {p.data['name']}\nFiche :\n" + ("\n".join(voice_chat.sheet_lines(sheet)) or "(vide)")
    got = voice_chat.ask_json(VOICE_TASK, user, _schema("description", "resume", "registre"))
    desc = str(got.get("description") or "").strip()
    if not desc:
        raise ChainError(f"le modèle de texte n'a pas décrit de voix : {got}")
    reg = str(got.get("registre") or "").strip().lower()
    return {"description": desc, "resume": str(got.get("resume") or "").strip(),
            "registre": reg if reg in ("grave", "medium", "aigu") else "medium"}


def draft_play_state(p: Project, text: str, context: str = "") -> dict:
    """L'état de jeu d'une réplique : {etat (fr), instruct (en)}."""
    if voice_chat.llm_is_stub():
        return {"etat": "Il le dit simplement, sans forcer, à quelqu'un qu'il connaît.",
                "instruct": "Say it plainly, calm and natural, moderate pace."}
    v = state(p)
    user = (f"Personnage : {p.data['name']}\nFiche :\n" + "\n".join(voice_chat.sheet_lines(p.sheet))
            + f"\nSa voix : {v.get('description_fr') or v.get('description')}\n"
            + (f"Situation : {context}\n" if context.strip() else "")
            + f"Réplique : « {text} »")
    got = voice_chat.ask_json(PLAY_TASK, user, _schema("etat", "instruct"))
    return {"etat": str(got.get("etat") or "").strip(), "instruct": str(got.get("instruct") or "").strip()}


def translate_direction(p: Project, direction: str) -> str:
    """Une direction donnée en français devient une consigne anglaise
    pour le moteur (qui suit mieux l'anglais) ; en factice, telle quelle."""
    if voice_chat.llm_is_stub():
        return direction
    got = voice_chat.ask_json(
        "Traduis cette direction d'acteur en une consigne ANGLAISE concrète pour un moteur de voix : émotion, "
        "intensité, débit, volume, qualité de voix. Ne décris pas le timbre. Rends {\"instruct\": \"...\"}.",
        direction, _schema("instruct"), temperature=0.2)
    return str(got.get("instruct") or direction).strip()


# ── audition ───────────────────────────────────────────────────────

def _write_take(p: Project, rel: str, take, sidecar: dict) -> dict:
    dest = p.path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(take.wav)
    info = {**sidecar, "seed": take.seed, "engine": take.engine, "model": take.meta.get("model", ""),
            "gen_s": round(take.gen_s, 2), "duration_s": round(take.duration_s, 2), "rtf": take.rtf, "at": now()}
    dest.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def set_description(p: Project, description: str, resume: str = "", registre: str = "") -> None:
    v = state(p)
    v["description"] = description.strip()
    if resume:
        v["description_fr"] = resume.strip()
    if registre:
        v["register"] = registre


def design(p: Project, *, n: int = 4, text: str | None = None, seed: int = 1, report=lambda pr, m: None) -> list[dict]:
    """N candidates sur le paragraphe d'essai, une graine chacune."""
    v = state(p)
    if not v["description"]:
        raise ChainError("aucune description de voix : donne-la, ou laisse le modèle de texte l'écrire depuis la fiche")
    text = (text or TEST_TEXT).strip()
    eng = voice_engine.engine()
    made = []
    for i in range(n):
        report(i / n, f"voix {i + 1}/{n}")
        take = eng.design(text, v["description"], seed + i)
        rel = f"voice/cand-{len(v['candidates']) + 1:03d}.wav"
        info = _write_take(p, rel, take, {"description": v["description"], "text": text})
        entry = {"file": rel, "seed": take.seed, "at": info["at"], "text": text, "description": v["description"],
                 "engine": take.engine, "duration_s": info["duration_s"], "gen_s": info["gen_s"]}
        v["candidates"].append(entry)
        p.save()
        made.append(entry)
        print(f"voix {rel} : graine {take.seed}, {info['duration_s']} s en {info['gen_s']} s ({take.engine})")
    report(1.0, f"{n} voix")
    return made


def _card(p: Project, chosen: dict) -> str:
    v = state(p)

    def q(s) -> str:
        return json.dumps(str(s or ""), ensure_ascii=False)

    return "\n".join([
        f"identite: {q(p.data['name'] + ' — voix conçue (Qwen3-TTS VoiceDesign), Character Factory')}",
        f"registre: {v.get('register') or 'medium'}",
        "langue: fr",
        "moteur_prefere: qwen3-tts-base",
        f"seed_figee: {chosen.get('seed')}",
        f"invariants: {q(v.get('description_fr') or v.get('description'))}",
        f"description: {q(chosen.get('description') or v.get('description'))}",
        f"personnage: {p.data['slug']}",
        f"source: {chosen['file']}",
        "",
    ])


def lock(p: Project, candidate: str) -> str:
    v = state(p)
    if v.get("locked"):
        raise ChainError("la voix est déjà verrouillée : elle fait autorité pour la conversation et le jeu. Pour en "
                         "changer, libère-la d'abord (voice_unlock / `./usine voix-libre`)")
    chosen = p._pick(v["candidates"], str(candidate), "voix")
    dest = p.path("voice/ref_neutral.wav")
    shutil.copy2(p.path(chosen["file"]), dest)
    # Le clonage ICL suit la transcription de la référence mot à mot : si
    # VoiceDesign a sauté un mot du paragraphe (vu le 27/09 : « Bon, »
    # manquant), le texte demandé ment. On transcrit ce qui a été dit
    # (Kyutai STT, par le service vocal) ; à défaut, le texte demandé.
    heard = voice_engine.engine().transcribe(dest.read_bytes())
    text = heard or chosen["text"].strip()
    p.path("voice/ref_neutral.txt").write_text(text + "\n", encoding="utf-8")
    p.path("voice/card.yaml").write_text(_card(p, chosen), encoding="utf-8")
    p.path("voice/consentement.txt").write_text(CONSENT, encoding="utf-8")
    v.update(locked=p.rel(dest), locked_text=text, locked_at=now(), locked_from=chosen["file"],
             locked_seed=chosen.get("seed"), locked_text_source="stt" if heard else "demandé")
    if chosen.get("description"):
        v["description"] = chosen["description"]
    p.save()
    return v["locked"]


def unlock(p: Project) -> str | None:
    """Libère la voix : la référence part dans `voice/archive/`, les
    prises déjà faites restent (mesurées contre l'ancienne voix)."""
    v = state(p)
    if not v.get("locked"):
        return None
    stamp = now().replace(":", "").replace("-", "")[:15]
    archive = p.dir("voice/archive")
    for name in ("ref_neutral.wav", "ref_neutral.txt", "card.yaml"):
        src = p.path(f"voice/{name}")
        if src.is_file():
            shutil.move(str(src), archive / f"{stamp}-{name}")
    old = v["locked"]
    v.update(locked=None, locked_text=None, locked_at=None, locked_from=None, locked_seed=None)
    v.setdefault("unlocked", []).append({"from": old, "at": now(), "archive": f"voice/archive/{stamp}-ref_neutral.wav"})
    p.save()
    return old


# ── jeu ────────────────────────────────────────────────────────────

def line(p: Project, text: str, *, direction: str = "", context: str = "", takes: int = 3, seed: int = 1,
         clone: bool = True, line_id: str | None = None, report=lambda pr, m: None) -> dict:
    """Une réplique jouée : `takes` prises « jeu » par VoiceDesign
    (description + consigne), les plus proches de la référence parmi
    plusieurs essais, plus une prise « voix » clonée si `clone`."""
    v = state(p)
    ref, _ = require_locked(p)
    text = text.strip()
    if line_id:
        entry = next((ln for ln in v["lines"] if ln["id"] == line_id), None)
        if entry is None:
            raise ChainError(f"réplique inconnue : {line_id}")
        text = entry["text"]
    else:
        if not text:
            raise ChainError("une réplique vide ne se joue pas")
        entry = {"id": f"l{len(v['lines']) + 1:03d}", "text": text, "direction": direction.strip(),
                 "play_state": "", "instruct": "", "context": context.strip(), "takes": [], "kept": None,
                 "at": now()}
        report(0.02, "état de jeu")
        if direction.strip():
            entry["play_state"] = direction.strip()
            entry["instruct"] = translate_direction(p, direction.strip())
        else:
            got = draft_play_state(p, text, context)
            entry["play_state"], entry["instruct"] = got["etat"], got["instruct"]
        v["lines"].append(entry)
        p.save()
        print(f"état de jeu : {entry['play_state']}")
    instruct = f"{v['description'].rstrip('. ')}. {entry['instruct']}".strip()
    eng = voice_engine.engine()
    # VoiceDesign rejoue la description à chaque prise : le jeu suit la
    # consigne, mais le timbre dérive d'une graine à l'autre (banc du 27/09
    # sur Kévin : cosinus ECAPA de 0,11 à 0,64 contre la référence, un clone
    # à 0,82-0,85). On tire donc plus de prises qu'on n'en garde, et on
    # garde celles qui sont restées sa voix — comme les vues, mesurées et
    # relancées jusqu'à ce que l'angle tienne.
    floor = float(config.setting("voice_min_similarity", "0.55"))
    budget = max(takes, takes * int(config.setting("voice_line_attempts", "3")))
    tried = []
    for i in range(budget):
        if sum(1 for s, _ in tried if s is not None and s >= floor) >= takes:
            break
        report(0.05 + 0.85 * i / budget, f"essai {i + 1} (jusqu'à {budget}), {takes} à garder")
        take = eng.design(text, instruct, seed + i)
        sim = eng.similarity(take.wav, ref)
        tried.append((sim, take))
        print(f"essai {i + 1} : graine {take.seed}, similarité {sim}, {take.duration_s:.2f} s en {take.gen_s:.1f} s")
    entry.setdefault("attempts", []).extend(s for s, _ in tried)
    chosen = sorted(tried, key=lambda t: -(t[0] if t[0] is not None else -1))[:takes]
    if clone:
        # La prise « voix » : le clone de la référence, timbre tenu, jeu
        # plus neutre (qwen-tts 0.1.1 ne prend pas de consigne au clonage).
        report(0.92, "prise clonée")
        take = eng.clone(text, ref, v.get("locked_text") or "", seed)
        chosen.append((eng.similarity(take.wav, ref), take))
    for k, (sim, take) in enumerate(chosen):
        kind = "voix" if clone and k == len(chosen) - 1 else "jeu"
        rel = f"voice/lines/{entry['id']}/take-{len(entry['takes']) + 1}.wav"
        info = _write_take(p, rel, take, {"text": text, "instruct": instruct if kind == "jeu" else "",
                                          "similarity": sim, "kind": kind})
        entry["takes"].append({"file": rel, "seed": take.seed, "at": info["at"], "similarity": sim, "kind": kind,
                               "engine": take.engine, "duration_s": info["duration_s"], "gen_s": info["gen_s"]})
        print(f"prise {rel} ({kind}) : graine {take.seed}, similarité {sim}")
    p.save()
    report(1.0, f"{len(chosen)} prises")
    return entry


def keep(p: Project, line_id: str, take: str) -> str:
    v = state(p)
    entry = next((ln for ln in v["lines"] if ln["id"] == line_id), None)
    if entry is None:
        raise ChainError(f"réplique inconnue : {line_id}")
    chosen = p._pick(entry["takes"], str(take), "prise")
    entry["kept"] = chosen["file"]
    p.save()
    return chosen["file"]


# ── conversation écrite, réponse dite ──────────────────────────────

def chat_messages(p: Project, message: str, history=None) -> list[dict]:
    if not str(message or "").strip():
        raise ChainError("un message vide ne se dit pas")
    system = voice_chat.persona(p.data["name"], p.sheet, p.data.get("notes"))
    return [{"role": "system", "content": system}, *voice_chat.history_messages(history),
            {"role": "user", "content": str(message).strip()[:2000]}]


def say(p: Project, text: str, seed: int, name: str, fast: bool = True) -> str | None:
    """Dit un texte de la voix verrouillée ; rend le chemin relatif du WAV,
    ou None si la voix n'est pas verrouillée ou qu'il n'y a rien à dire."""
    ref = locked_ref(p)
    spoken = voice_chat.speakable(text)
    if ref is None or not spoken:
        return None
    take = voice_engine.engine().clone(spoken, ref[0], ref[1], seed, fast=fast)
    rel = f"voice/chat/{name}.wav"
    dest = p.path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(take.wav)
    return rel
