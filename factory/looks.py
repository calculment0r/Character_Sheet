"""Les looks : le visage verrouillé, retouché.

Un look est une retouche du visage verrouillé par l'édition d'identité de
Krea 2 (Identity Edit v1.2, `krea2.py`) : du maquillage de soirée, une
fine cicatrice sur la pommette gauche, une autre coiffure. Il garde
l'identité, et ne remplace jamais le visage verrouillé : c'est une image
de plus, rangée à côté (`face/looks/<id>.png`) — la règle « le visage ne
se verrouille qu'une fois » tient (`project.py`).

La consigne de Cal, en français, passe par le modèle de texte
(`brief.read_edit`) : une consigne anglaise précise, qui dit le côté du
corps ; ce qui ne bouge pas (même personne, même lumière, même fond)
reste écrit ici. Chaque rendu passe au contrôle d'identité de la planche
(`presentation.identity`), relancé une fois sous le seuil, le meilleur
gardé.

Manifeste : `face.looks` = [{id, name, prompt, prompt_en, file, status,
at, seed, score, error}]. `prompt` est la consigne de Cal, `prompt_en`
celle envoyée à Krea 2 ; `file` vaut None tant que rien n'est rendu.
L'entrée naît `pending` dès la demande (le studio la crée avant de mettre
le travail en file), passe `ready` au rendu, `error` s'il échoue, s'il est
annulé ou si le studio s'est arrêté pendant. Un look retiré va à la
corbeille du personnage (`.corbeille/face/looks/`) et sort de la liste.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from . import brief, config
from .project import ChainError, Project, now

LOOK_MIN = 0.80         # le seuil des expressions calmes (`presentation.EXPR_MIN`)
LOOK_TRIES = 2
AREA = 1024 * 1024      # la taille d'une édition du visage (`krea2.FACE`)


def looks(p: Project) -> list[dict]:
    return p.face.setdefault("looks", [])


def find(p: Project, look_id: str) -> dict:
    entry = next((e for e in looks(p) if e["id"] == look_id), None)
    if entry is None:
        have = ", ".join(e["id"] for e in looks(p)) or "aucun"
        raise ChainError(f"look inconnu, ou retiré : {look_id} (existants : {have})")
    return entry


def text_look(change: str) -> str:
    """Pour Krea 2 (édition d'identité) : la retouche seule, puis ce qui ne
    bouge pas — l'âge nommé, comme pour les expressions (sans lui, une
    retouche vieillit, essai du 28/09). Essai du 28/09 au soir (cicatrice
    sur la pommette gauche) : le bon côté, mais la tête tournée de 3/4
    pour la montrer — d'où la tête face à l'objectif, écrite en prose."""
    change = change.strip().rstrip(".")
    change = change[:1].upper() + change[1:]
    return (f"{change}. Change nothing else: the same person, face, identity, age, expression, framing, light and "
            "plain background; the head still faces the camera straight on, exactly as in the source photo. "
            "Photograph with natural skin texture.")


def submit(p: Project, prompt: str, *, name: str = "", look_id: str | None = None,
           seed: int | None = None) -> dict:
    """La demande, avant le rendu : l'entrée passe `pending`. Un `look_id`
    encore là (une relance) repart de son entrée ; sinon, un nouveau look."""
    p.require_face()
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ChainError("un look se décrit : écris la retouche (« du maquillage de soirée »)")
    entry = next((e for e in looks(p) if e["id"] == look_id), None) if look_id else None
    if entry is None:
        entry = {"id": f"look-{uuid.uuid4().hex[:6]}", "file": None}
        looks(p).append(entry)
    elif entry.get("prompt") != prompt:
        entry.pop("prompt_en", None)
        entry.pop("prompt_read", None)
    entry.update(name=str(name or "").strip() or entry.get("name") or brief.short_name(prompt), prompt=prompt,
                 status="pending", at=now(), seed=seed, error=None)
    p.save()
    return entry


def read(p: Project, look_id: str, llm=lambda: None, say=lambda m: None, *, keep_name: bool = False) -> dict:
    """La consigne de Cal devenue consigne anglaise (`brief.LOOK_TASK`) ;
    une consigne déjà anglaise passe telle quelle. Rien à relire si elle
    n'a pas bougé. `keep_name` : le nom vient de Cal, le modèle n'y touche pas."""
    entry = find(p, look_id)
    text = entry["prompt"]
    if entry.get("prompt_en") and entry.get("prompt_read") == text:
        return entry
    if brief.french(text):
        say("le modèle de texte écrit la retouche")
        if config.backend("brief") != "stub":
            llm()
        got = brief.read_edit(brief.LOOK_TASK, text, sheet=p.sheet)
        entry["prompt_en"] = got["prompt"]
        if not keep_name:
            entry["name"] = got["name"]
    else:
        entry["prompt_en"] = text
    entry["prompt_read"] = text
    p.save()
    say(f"look : {entry['prompt_en']}")
    return entry


def size_like(path: Path, area: int = AREA) -> tuple[int, int]:
    """La sortie au rapport de la source (règle du LoRA : sinon l'identité
    se dégrade), vers 1 Mpx, en multiples de 16."""
    from PIL import Image

    w, h = Image.open(path).size
    k = (area / (w * h)) ** 0.5
    return max(16, round(w * k / 16) * 16), max(16, round(h * k / 16) * 16)


def render(p: Project, look_id: str, *, seed: int, report=lambda pr, m: None) -> dict:
    """Le look rendu : le visage verrouillé en source, la consigne, le
    contrôle d'identité ; une relance sous `LOOK_MIN`, la meilleure gardée."""
    from . import krea2, presentation
    from . import stubs as sketches
    from .chain import identity_seed

    locked = p.path(p.require_face())
    entry = find(p, look_id)
    text = text_look(entry.get("prompt_en") or entry["prompt"])
    size = size_like(locked)
    folder = p.dir("face/looks")
    work = folder / f".{look_id}"
    stub = config.backend("portrait") == "stub"
    rounds = 1 if stub else LOOK_TRIES
    tries: list[dict] = []
    for k in range(rounds):
        s = seed + k
        dest = work / f"essai-{k + 1}.png"
        print(f"  look {look_id} · essai {k + 1} · graine {s} · Krea 2 · {text}")
        t0 = time.monotonic()
        krea2.generate(prompt=text, refs=[locked], dest=dest, seed=s, size=size,
                       report=lambda pr, m, k=k: report(0.95 * (k + pr) / rounds, m),
                       stub=lambda: sketches.portrait(size, seed=identity_seed(p), label=f"FACTICE · {entry['name']}"))
        sc = presentation.identity(locked, [dest])[0]
        tries.append({"file": dest, "seed": s, "secs": round(time.monotonic() - t0, 1), **sc})
        if sc["score"] is not None:
            print(f"    identité {sc['score']:.3f}")
        if not sc.get("checked") or (sc.get("score") or 0.0) >= LOOK_MIN:
            break
    best = max(tries, key=lambda t: t.get("score") if t.get("score") is not None else -1.0)
    final = folder / f"{look_id}.png"
    Path(best["file"]).replace(final)
    for t in tries:
        if Path(t["file"]).exists():
            Path(t["file"]).unlink()
    if not any(e is entry for e in looks(p)):
        # Retiré pendant le rendu : l'image rejoint la corbeille, rien ne revient.
        print(f"  look {look_id} retiré pendant le rendu : l'image va à la corbeille")
        p.trash(p.rel(final))
        return {**entry, "status": "removed"}
    entry.update(file=p.rel(final), status="ready", seed=best["seed"], score=best.get("score"), at=now(),
                 error=None, engine="krea2", backend=config.backend("portrait"), size=list(size),
                 secs=round(sum(t["secs"] for t in tries), 1),
                 tries=[{"seed": t["seed"], "score": t.get("score"), "secs": t["secs"]} for t in tries])
    if best.get("score") is not None and best["score"] < LOOK_MIN:
        entry["below_floor"] = True
    else:
        entry.pop("below_floor", None)
    p.save()
    report(1.0, "look rendu")
    return entry


def fail(p: Project, look_id: str | None, error: str) -> None:
    """Un rendu qui n'aboutit pas : l'entrée le dit, l'image d'avant reste."""
    entry = next((e for e in looks(p) if e["id"] == look_id), None)
    if entry is None or entry.get("status") != "pending":
        return
    entry.update(status="error", error=str(error or "échec"), at=now())
    p.save()


def interrupted(p: Project) -> bool:
    """Au démarrage du studio : un look encore `pending` ne finira jamais."""
    stale = [e for e in looks(p) if e.get("status") == "pending"] if p.face.get("looks") else []
    for e in stale:
        e.update(status="error", error="interrompu : le studio s'est arrêté pendant le rendu", at=now())
    if stale:
        p.save()
    return bool(stale)


def remove(p: Project, look_id: str) -> dict:
    """Le look à la corbeille du personnage ; l'entrée sort de la liste."""
    entry = find(p, look_id)
    trashed = p.trash(entry["file"]) if entry.get("file") else None
    looks(p).remove(entry)
    p.save()
    return {"removed": look_id, "trash": trashed}
