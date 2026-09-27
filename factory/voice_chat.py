"""Parler avec un personnage : ce qui est commun au studio et au service vocal.

Le studio (DGX2, bibliothèque standard seule) sert la conversation écrite
avec réponse dite ; le service vocal (`voice_server.py`, DGX1) sert la
conversation en direct au micro. Les deux prennent ici la même chose :

  - le prompt système qui fait parler le personnage depuis sa fiche
    (qui il est, comment il parle), réglé pour l'oral : phrases courtes,
    aucune didascalie, rien qui ne se prononce pas ;
  - le modèle de texte en flux (Ollama, `/api/chat`, sans réflexion) ;
  - le découpage de ce flux en morceaux à dire : le premier très court,
    pour que la voix parte vite, les suivants à la fin des phrases ;
  - le nettoyage de ce qui ne doit pas être lu à voix haute.

Le modèle de texte de la conversation n'est pas celui de l'étage
Identité : `voice_llm_url` et `voice_llm_model` (défaut : le serveur
Ollama du studio, `qwen3-vl:30b-a3b-instruct`, rapide parce que seuls 3 B de ses
paramètres travaillent à chaque mot). En factice (`FACTORY_BRIEF=stub`),
une réponse écrite d'avance, pour tester sans modèle.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import config
from .project import ChainError

# ── réglages ───────────────────────────────────────────────────────


def llm_url() -> str:
    return config.setting("voice_llm_url", config.setting("llm_url", "http://127.0.0.1:11434")).rstrip("/")


def llm_model() -> str:
    # qwen3:30b-a3b d'Ollama est la version « Thinking 2507 » : elle réfléchit
    # à voix haute quoi qu'on lui dise (think: false, /no_think), 30 s avant
    # le premier mot. La variante instruct du même MoE (3 B actifs) répond
    # tout de suite ; elle est multimodale, peu importe ici.
    return config.setting("voice_llm_model", "qwen3-vl:30b-a3b-instruct")


def llm_ctx() -> int:
    # Toujours explicite : sans num_ctx, Ollama 0.20 réserve le contexte
    # maximal du modèle (mistral-nemo : 67 Go), de quoi geler un Spark.
    return int(config.setting("voice_llm_ctx", "8192"))


def llm_is_stub() -> bool:
    return config.backend("brief") == "stub"


# ── le personnage ──────────────────────────────────────────────────

# Les champs de la fiche qui disent qui parle, dans l'ordre où on les
# présente au modèle, avec leur nom en clair (js/schema.js).
PERSONA_FIELDS = (
    ("age", "âge"), ("gender", "genre"), ("ethnicity", "origine"), ("height", "taille"),
    ("body_type", "carrure"), ("role", "rôle"), ("archetype", "archétype"),
    ("personality_traits", "personnalité"), ("core_theme", "thème profond"),
    ("emotional_range", "registre émotionnel"), ("behavior_notes", "comportement"),
    ("speech_style", "façon de parler, accent"), ("face_description", "visage"),
    ("default_outfit_description", "tenue"),
)


def sheet_lines(sheet: dict) -> list[str]:
    return [f"- {label} : {str(sheet.get(k)).strip()}" for k, label in PERSONA_FIELDS if str(sheet.get(k) or "").strip()]


def persona(name: str, sheet: dict, notes: list[str] | None = None) -> str:
    """Le prompt système de la conversation. Tout ce que le modèle écrit
    sera prononcé : la règle d'or est de n'écrire que de la parole."""
    who = "\n".join(sheet_lines(sheet)) or "- (fiche presque vide : invente une personnalité cohérente avec le nom)"
    extra = "\n".join(f"- {n}" for n in (notes or [])[:8] if str(n).strip())
    return f"""Tu es {name}. Tu n'es pas un assistant : tu es ce personnage, et tu parles à voix haute avec la personne en face de toi.

Qui tu es :
{who}
{('Détails : ' + chr(10) + extra) if extra else ''}
Comment tu réponds :
- en français, comme à l'oral, avec ta façon de parler à toi (vocabulaire, rythme, tics de langage) ;
- court : une à trois phrases, rarement plus ; une question courte appelle une réponse courte ;
- tout ce que tu écris sera dit par ta voix : pas de didascalie, pas d'action entre astérisques ou parenthèses,
  pas de liste, pas de titre, pas d'émoticône, pas de mise en forme ; les nombres en toutes lettres si c'est court ;
- tu restes toi-même d'un bout à l'autre, avec ton humeur et tes opinions ; tu peux relancer, refuser, plaisanter.
""".strip()


# ── ce qui ne se dit pas ───────────────────────────────────────────

_STAGE = re.compile(r"\*[^*]{0,200}\*|\([^)]{0,200}\)|\[[^\]]{0,200}\]|<[^>]{0,80}>")
_MARKUP = re.compile(r"[#*_`~>|]+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def speakable(text: str) -> str:
    """Retire ce qui ne doit pas être lu : didascalies, balises, mise en
    forme, émoticônes. Rend une chaîne vide s'il ne reste rien à dire."""
    text = _STAGE.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = _MARKUP.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if re.search(r"\w", text) else ""


class Chunker:
    """Le flux du modèle de texte, en morceaux à dire.

    Le premier morceau part à la première ponctuation passé une quinzaine
    de signes : c'est lui qui fixe la latence perçue, la voix doit partir
    tôt. Les suivants attendent une fin de phrase, pour une prosodie
    entière ; une phrase trop longue se coupe à une virgule, puis à une
    espace."""

    FIRST_MIN, NEXT_MIN, COMMA_MIN, HARD_MAX = 15, 40, 110, 200

    def __init__(self) -> None:
        self.buf = ""
        self.count = 0

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        while True:
            cut = self._cut()
            if cut is None:
                return out
            piece, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if piece:
                self.count += 1
                out.append(piece)

    def flush(self) -> list[str]:
        piece, self.buf = self.buf.strip(), ""
        if piece:
            self.count += 1
            return [piece]
        return []

    def _cut(self) -> int | None:
        b = self.buf
        if self.count == 0:
            for m in re.finditer(r"[.!?…,;:]+[»\"')]*\s", b):
                if m.end() >= self.FIRST_MIN:
                    return m.end()
            if len(b) > 60:
                sp = b.rfind(" ", self.FIRST_MIN, 60)
                return sp + 1 if sp > 0 else 60
            return None
        for m in re.finditer(r"[.!?…]+[»\"')]*\s", b):
            if m.end() >= self.NEXT_MIN:
                return m.end()
        for m in re.finditer(r"[,;:]\s", b):
            if m.end() >= self.COMMA_MIN:
                return m.end()
        if len(b) > self.HARD_MAX:
            sp = b.rfind(" ", 0, self.HARD_MAX)
            return sp + 1 if sp > 0 else self.HARD_MAX
        return None


# ── le modèle de texte ─────────────────────────────────────────────

def _post(url: str, body: dict, timeout: float):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        raise ChainError(f"le modèle de texte de la voix ne répond pas ({llm_url()}, {llm_model()}) : {exc}") from exc


def stream(messages: list[dict], *, temperature: float = 0.8, max_tokens: int = 260, stop=lambda: False,
           on_response=None):
    """Les morceaux de texte du modèle, au fil de l'eau. `stop()` vrai
    arrête la lecture (l'utilisateur a repris la parole)."""
    if llm_is_stub():
        yield from _stub_stream(messages)
        return
    body = {"model": llm_model(), "messages": messages, "stream": True, "think": False, "keep_alive": "30m",
            "options": {"temperature": temperature, "num_predict": max_tokens, "top_p": 0.9, "num_ctx": llm_ctx()}}
    with _post(f"{llm_url()}/api/chat", body, timeout=120) as res:
        if on_response:
            on_response("res", res)          # l'appelant peut fermer la connexion pour couper court
        for raw in res:
            if stop():
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("error"):
                raise ChainError(f"modèle de texte : {msg['error']}")
            delta = (msg.get("message") or {}).get("content") or ""
            if delta:
                yield delta
            if msg.get("done"):
                return


def complete(messages: list[dict], **kw) -> str:
    return "".join(stream(messages, **kw)).strip()


def ask_json(task: str, user: str, schema: dict, *, temperature: float = 0.7, timeout: float = 300) -> dict:
    """Une réponse JSON contrainte par un schéma (description de voix,
    état de jeu). En factice, None : l'appelant fabrique sa valeur."""
    body = {"model": llm_model(), "messages": [{"role": "system", "content": task}, {"role": "user", "content": user}],
            "format": schema, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": llm_ctx()}}
    with _post(f"{llm_url()}/api/chat", body, timeout=timeout) as res:
        reply = json.loads(res.read())
    try:
        return json.loads(reply["message"]["content"])
    except (KeyError, ValueError) as exc:
        raise ChainError(f"réponse illisible du modèle de texte : {str(reply)[:300]}") from exc


def _stub_stream(messages: list[dict]):
    """Une réponse factice, au fil de l'eau, pour tester sans modèle."""
    said = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    said = str(said).strip()[:80]
    text = (f"Réponse factice, tu m'as dit : {said or 'rien'}. "
            "Le vrai modèle de texte n'est pas branché, alors je réponds toujours pareil.")
    for word in re.findall(r"\S+\s*", text):
        time.sleep(0.005)
        yield word


def history_messages(history, limit: int = 16) -> list[dict]:
    """L'historique envoyé par la page, réduit à des tours texte valides."""
    out = []
    for m in history if isinstance(history, list) else []:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            if m["content"].strip():
                out.append({"role": m["role"], "content": m["content"].strip()[:2000]})
    return out[-limit:]
