"""Le service vocal : `./usine voix-serveur` (ou `python -m factory.voice_server`).

Tourne sur DGX1 par défaut : la conversation en direct ne doit pas
attendre derrière une image de DGX2. FastAPI + uvicorn, dans son propre
environnement (`~/cf-voice-env` : qwen-tts, torch, fastapi, websockets).

  GET  /health                 le moteur, l'oreille, le modèle de texte, le studio
  POST /tts/design             {text, instruct, seed?, language?} → WAV (Qwen3-TTS VoiceDesign)
  POST /tts/clone              {text, ref_b64, ref_text, seed?} → WAV (Qwen3-TTS Base, ICL)
  POST /similarity             {a_b64, b_b64} → {cosine} (ECAPA)
       les WAV portent leurs mesures en en-têtes : x-seed, x-gen-s, x-rtf, x-duration-s, x-model
  WS   /ws/chat?slug=<perso>   la conversation en direct (protocole plus bas)
  GET  /voix.html, /js/…, /assets/…   la page de test
  *    /api/…, /files/…        relayés vers le studio (`voice_studio_url`) : servie ici en HTTPS,
                               la page a le micro ET tout le studio sur la même origine

HTTP sur `--port` (8770), HTTPS sur `--https` (8771, certificat auto-signé,
`tls.py`) : le navigateur ne prête le micro qu'en contexte sûr.

La conversation en direct — une cascade, faute de modèle full-duplex
ouvert en français :

  micro (PCM 16 bits, 24 kHz, mono) → Kyutai STT 1B en_fr en flux
  (le serveur existant `~/voix/serveur-kyutai-stt.py`, :5930, avec sa
  fin de tour sémantique) → modèle de texte en flux (Ollama,
  `qwen3-vl:30b-a3b-instruct`, prompt du personnage) → morceaux de phrase → Qwen3-TTS
  Base, la voix verrouillée clonée → PCM vers le navigateur.

  Page → service :  binaire = PCM int16 24 kHz mono ;
                    {"type":"micro","on":true,"cran":"vif|normal|patient"} ;
                    {"type":"texte","message":"…"} ; {"type":"stop"} ;
                    {"type":"lecture_finie"}
  Service → page :  {"type":"pret",…} ; {"type":"ecoute",…} ; {"type":"mot","texte"} ;
                    {"type":"tour","texte","source"} ; {"type":"texte","delta"} ;
                    {"type":"audio","i","texte","sr","ms"} suivi d'un message binaire (PCM int16) ;
                    {"type":"fin","texte","mesures"} ; {"type":"stop","raison"} ; {"type":"erreur","message"}

  Coupure (barge-in) : des mots reconnus pendant que le personnage parle
  arrêtent sa réponse (`stop`, la page vide sa file de lecture) ; ils
  commencent le tour suivant. Casque conseillé : sans lui, l'oreille
  entend le personnage (l'annulation d'écho du navigateur aide, sans
  garantie).
"""


# Pas de `from __future__ import annotations` ici : FastAPI lit les annotations
# des routes, et les modèles pydantic sont locaux à build_app.
import asyncio
import base64
import json
import mimetypes
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from . import config, voice_chat
from .voice_engine import StubEngine, wav_bytes, wav_samples

STATIC = ("voix.html",)
EBML = b"\x1a\x45\xdf\xa3"      # début d'un fichier webm : la prise « appuyer pour parler » de js/parler.js
STATIC_DIRS = ("js", "assets")


def studio_url() -> str:
    return config.setting("voice_studio_url", "http://127.0.0.1:8765").rstrip("/")


def stt_url() -> str:
    return config.setting("voice_stt_url", "ws://127.0.0.1:5930/ecoute")


def barge_words() -> int:
    return int(config.setting("voice_barge_words", "2"))


def turn_grace() -> float:
    """Combien de temps après une fin de tour un mot qui arrive la complète
    au lieu d'en ouvrir une autre."""
    return float(config.setting("voice_turn_grace_s", "0.9"))


# ── le personnage, lu dans le studio ───────────────────────────────

def fetch_character(slug: str) -> dict:
    base = studio_url()
    q = urllib.parse.quote(slug)
    with urllib.request.urlopen(f"{base}/api/characters/{q}", timeout=10) as res:
        c = json.loads(res.read())["character"]
    v = c.get("voice") or {}
    ref = None
    if v.get("locked"):
        with urllib.request.urlopen(f"{base}/files/{q}/{urllib.parse.quote(v['locked'])}", timeout=20) as res:
            ref = res.read()
    return {"slug": c["slug"], "name": c["name"], "sheet": c.get("identity") or {}, "notes": c.get("notes") or [],
            "ref": ref, "ref_text": v.get("locked_text") or "", "seed": v.get("locked_seed") or 1}


# ── une oreille factice ────────────────────────────────────────────

class StubEar:
    """Pour la page sans modèle : de la parole (du bruit au-dessus d'un
    seuil) suivie de 0,8 s de silence fait un tour, « parole factice »."""

    def __init__(self) -> None:
        self.speech_s = 0.0
        self.quiet_s = 0.0

    def feed(self, pcm: bytes) -> list[dict]:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if not len(x):
            return []
        dur = len(x) / 24000
        loud = float(np.sqrt(np.mean(x ** 2))) > 0.02
        if loud:
            self.speech_s += dur
            self.quiet_s = 0.0
            return []
        self.quiet_s += dur
        if self.speech_s >= 0.3 and self.quiet_s >= 0.8:
            self.speech_s = self.quiet_s = 0.0
            return [{"type": "mot", "texte": " parole factice"}, {"type": "fin-de-tour", "pr": 1.0}]
        return []


# ── une conversation ───────────────────────────────────────────────

class Session:
    def __init__(self, ws, engine, slug: str, stub: bool) -> None:
        self.ws, self.engine, self.slug, self.stub = ws, engine, slug, stub
        self.char: dict = {}
        self.history: list[dict] = []
        self.words: list[str] = []
        self.words_while_speaking = 0
        self.last_word_t: float | None = None
        self.reply_task: asyncio.Task | None = None
        self.cancel: threading.Event | None = None
        self.speaking_until = 0.0
        self.stt = None
        self.stt_task: asyncio.Task | None = None
        self.stub_ear: StubEar | None = None
        self.out = asyncio.Lock()
        self.ptt: bytearray | None = None     # une prise webm/opus en cours (js/parler.js)
        self.turn_t: float | None = None      # fin du dernier tour dit, pour le compléter
        self.continued = 0
        self.audio_sent = False

    async def send(self, obj: dict) -> None:
        async with self.out:
            await self.ws.send_text(json.dumps(obj, ensure_ascii=False))

    async def send_audio(self, meta: dict, pcm: bytes) -> None:
        async with self.out:
            await self.ws.send_text(json.dumps(meta, ensure_ascii=False))
            await self.ws.send_bytes(pcm)

    # la boucle

    async def run(self) -> None:
        try:
            self.char = await asyncio.to_thread(fetch_character, self.slug)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            await self.send({"type": "erreur", "message": f"personnage « {self.slug} » introuvable au studio "
                                                          f"({studio_url()}) : {exc}"})
            return
        self.system = voice_chat.persona(self.char["name"], self.char["sheet"], self.char["notes"])
        await self.send({"type": "pret", "personnage": self.char["name"], "slug": self.char["slug"],
                         "voix": self.char["ref"] is not None, "moteur": self.engine.name,
                         "llm": {"model": voice_chat.llm_model(), "stub": voice_chat.llm_is_stub()},
                         "oreille": "factice" if self.stub else stt_url()})
        if self.char["ref"] is not None and hasattr(self.engine, "prompt"):
            # le prompt de clonage d'avance : le premier tour n'en paie pas le calcul
            await asyncio.to_thread(self._prepare)
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await self.audio_in(msg["bytes"])
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except ValueError:
                    continue
                await self.command(cmd)

    def _prepare(self) -> None:
        with self.engine.gpu:
            self.engine.prompt(self.char["ref"], self.char["ref_text"], True)

    async def close(self) -> None:
        await self.stop_reply()
        await self.close_ear()

    async def command(self, c: dict) -> None:
        kind = c.get("type")
        if kind == "micro":
            await (self.open_ear(str(c.get("cran") or "normal")) if c.get("on") else self.close_ear())
        elif kind == "texte" and str(c.get("message") or "").strip():
            await self.user_turn(str(c["message"]).strip(), "texte", time.monotonic(), None)
        elif kind == "stop":
            await self.interrupt("stop")
        elif kind == "lecture_finie":
            self.speaking_until = 0.0
        elif kind == "end":
            await self.ptt_turn()
        elif kind == "ping":
            await self.send({"type": "pong", "t": c.get("t")})

    # l'oreille

    async def open_ear(self, cran: str) -> None:
        await self.close_ear()
        self.words, self.words_while_speaking = [], 0
        if self.stub:
            self.stub_ear = StubEar()
            await self.send({"type": "ecoute", "oreille": "factice", "cran": cran})
            return
        import websockets

        try:
            self.stt = await websockets.connect(f"{stt_url()}?cran={cran}", max_size=None)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            await self.send({"type": "erreur", "message": f"oreille injoignable ({stt_url()}) : {exc} — lancer "
                                                          f"~/voix/demarre-stt.sh sur la machine de la voix"})
            return
        self.stt_task = asyncio.create_task(self.read_ear())

    async def close_ear(self) -> None:
        self.stub_ear = None
        if self.stt is not None:
            try:
                await self.stt.close()
            except Exception:  # noqa: BLE001
                pass
            self.stt = None
        if self.stt_task is not None:
            self.stt_task.cancel()
            self.stt_task = None

    async def audio_in(self, pcm: bytes) -> None:
        if self.ptt is not None or pcm[:4] == EBML:
            # Appuyer pour parler (js/parler.js, MediaRecorder) : on garde la
            # prise entière jusqu'à {"type":"end"}.
            if self.ptt is None:
                self.ptt = bytearray()
            self.ptt += pcm
            return
        if self.stub_ear is not None:
            for evt in self.stub_ear.feed(pcm):
                await self.on_ear(evt)
        elif self.stt is not None:
            try:
                await self.stt.send(pcm)
            except Exception as exc:  # noqa: BLE001 — l'oreille est tombée
                await self.send({"type": "erreur", "message": f"oreille perdue : {exc}"})
                await self.close_ear()

    async def read_ear(self) -> None:
        try:
            async for raw in self.stt:
                try:
                    await self.on_ear(json.loads(raw))
                except ValueError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "erreur", "message": f"oreille : {exc}"})

    def speaking(self) -> bool:
        return (self.reply_task is not None and not self.reply_task.done()) or time.monotonic() < self.speaking_until

    async def on_ear(self, evt: dict) -> None:
        kind = evt.get("type")
        if kind == "pret":
            await self.send({"type": "ecoute", "retard_ms": evt.get("retard_ms"), "pause_ms": evt.get("pause_ms"),
                             "cran": evt.get("cran"), "vad": evt.get("vad")})
        elif kind == "mot":
            word, now = evt.get("texte", ""), time.monotonic()
            self.last_word_t = now
            await self.send({"type": "mot", "texte": word})
            if (self.turn_t is not None and now - self.turn_t < turn_grace() and not self.audio_sent
                    and self.history and self.history[-1]["role"] == "user"):
                # La fin du tour qu'on vient de fermer : la tête de fin de tour
                # de Kyutai voit le silence avant que le texte, décalé de
                # 500 ms, ait fini de sortir (banc du 27/09 : le dernier mot
                # arrive ~250 ms après la fin de tour). On complète le tour et
                # on relance la réponse, sans rien couper de ce qui s'entend.
                self.history[-1]["content"] = (self.history[-1]["content"] + word).strip()
                self.continued += 1
                await self.stop_reply()
                await self.send({"type": "tour", "texte": self.history[-1]["content"], "source": "voix", "suite": True})
                self.reply_task = asyncio.create_task(self.reply(self.turn_t, now, "voix"))
                return
            self.words.append(word)
            if self.speaking():
                self.words_while_speaking += 1
                if self.words_while_speaking >= barge_words():
                    await self.interrupt("coupé")
        elif kind == "fin-de-tour":
            text = "".join(self.words).strip()
            self.words, self.words_while_speaking = [], 0
            if text:
                await self.user_turn(text, "voix", time.monotonic(), self.last_word_t)
                self.turn_t = time.monotonic()
        elif kind == "erreur":
            await self.send({"type": "erreur", "message": f"oreille : {evt.get('message')}"})

    # appuyer pour parler (js/parler.js)

    async def ptt_turn(self) -> None:
        """Une prise entière : transcrite, répondue, dite. Rend
        {"type":"transcript","text"}, puis {"type":"reply","text","audio":null}
        suivi du son de la réponse en WAV (un message binaire, jouable tel quel)."""
        data, self.ptt = bytes(self.ptt or b""), None
        if not data:
            return
        t0 = time.monotonic()
        try:
            heard = await asyncio.to_thread(self._transcribe_webm, data)
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "erreur", "message": f"prise illisible : {exc}"})
            return
        await self.send({"type": "transcript", "text": heard})
        if not heard:
            return
        self.history.append({"role": "user", "content": heard})
        t1 = time.monotonic()
        try:
            reply = await asyncio.to_thread(voice_chat.complete,
                                            [{"role": "system", "content": self.system}, *self.history[-16:]])
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "erreur", "message": str(exc)})
            return
        self.history.append({"role": "assistant", "content": reply})
        t2 = time.monotonic()
        spoken = voice_chat.speakable(reply)
        wav = None
        if spoken and self.char.get("ref") is not None:
            x, sr, _ = await asyncio.to_thread(self._clone, spoken, 0)
            wav = wav_bytes(x, sr)
        marks = {"source": "prise", "transcription_ms": int((t1 - t0) * 1000), "texte_ms": int((t2 - t1) * 1000),
                 "voix_ms": int((time.monotonic() - t2) * 1000), "total_ms": int((time.monotonic() - t0) * 1000)}
        _log(self.slug, marks, reply)
        await self.send({"type": "reply", "text": reply, "audio": None, "mesures": marks})
        if wav:
            async with self.out:
                await self.ws.send_bytes(wav)

    def _transcribe_webm(self, data: bytes) -> str:
        if self.stub:
            return "parole factice"
        import subprocess

        dec = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "24000", "-f", "wav",
                              "pipe:1"], input=data, capture_output=True, timeout=60)
        if dec.returncode != 0:
            raise ValueError(dec.stderr.decode("utf-8", "replace")[-300:])
        u = stt_url().replace("ws://", "http://").replace("wss://", "https://").rsplit("/", 1)[0]
        req = urllib.request.Request(f"{u}/stt", data=dec.stdout, method="POST", headers={"Content-Type": "audio/wav"})
        with urllib.request.urlopen(req, timeout=60) as res:
            return (json.loads(res.read()).get("texte") or "").strip()

    # la réponse

    async def stop_reply(self) -> bool:
        task, self.reply_task = self.reply_task, None
        if self.cancel is not None:
            self.cancel.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            return True
        return False

    async def interrupt(self, why: str) -> None:
        stopped = await self.stop_reply()
        if stopped or time.monotonic() < self.speaking_until:
            self.speaking_until = 0.0
            await self.send({"type": "stop", "raison": why})

    async def user_turn(self, text: str, source: str, t_turn: float, t_last_word: float | None) -> None:
        await self.interrupt("nouveau tour")
        self.turn_t, self.continued, self.audio_sent = None, 0, False
        self.history.append({"role": "user", "content": text})
        await self.send({"type": "tour", "texte": text, "source": source})
        self.reply_task = asyncio.create_task(self.reply(t_turn, t_last_word, source))

    async def settle(self, t0: float) -> None:
        """Avant de lancer le modèle sur un tour dit : attendre que le texte
        ait fini de sortir de l'oreille — une ponctuation finale, ou un
        court silence de mots, au plus `voice_settle_max_s` après la fin de
        tour. Un mot qui arrive pendant l'attente relance la réponse (et
        donc l'attente) avec le tour complété : on n'envoie au modèle
        qu'une question entière, au lieu de le relancer à chaque mot."""
        quiet = float(config.setting("voice_settle_quiet_s", "0.3"))
        cap = float(config.setting("voice_settle_max_s", "0.7"))
        while True:
            said = self.history[-1]["content"] if self.history else ""
            now = time.monotonic()
            if re.search(r"[.?!…]\s*$", said) or now - t0 >= cap or now - max(t0, self.last_word_t or t0) >= quiet:
                return
            await asyncio.sleep(0.03)

    async def reply(self, t0: float, t_last_word: float | None, source: str) -> None:
        if source == "voix":
            await self.settle(t0)
        loop = asyncio.get_running_loop()
        cancel = self.cancel = threading.Event()
        deltas: asyncio.Queue = asyncio.Queue()
        held: dict = {}
        messages = [{"role": "system", "content": self.system}, *self.history[-16:]]
        marks: dict = {"source": source}
        if self.continued:
            marks["tour_complete_apres_fin"] = self.continued
        if t_last_word is not None:
            marks["fin_de_tour_apres_dernier_mot_ms"] = int((t0 - t_last_word) * 1000)

        def produce() -> None:
            try:
                for d in voice_chat.stream(messages, stop=cancel.is_set, on_response=held.__setitem__):
                    loop.call_soon_threadsafe(deltas.put_nowait, ("d", d))
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(deltas.put_nowait, ("e", str(exc)))
            loop.call_soon_threadsafe(deltas.put_nowait, ("end", None))

        threading.Thread(target=produce, daemon=True).start()
        chunks: asyncio.Queue = asyncio.Queue()
        speaker = asyncio.create_task(self.speak(chunks, t0, t_last_word, marks))
        chunker, reply = voice_chat.Chunker(), ""
        try:
            while True:
                kind, val = await deltas.get()
                if kind == "d":
                    marks.setdefault("premier_mot_ms", int((time.monotonic() - t0) * 1000))
                    reply += val
                    await self.send({"type": "texte", "delta": val})
                    for piece in chunker.feed(val):
                        marks.setdefault("premier_morceau_ms", int((time.monotonic() - t0) * 1000))
                        await chunks.put(piece)
                elif kind == "e":
                    await self.send({"type": "erreur", "message": val})
                    break
                else:
                    break
            for piece in chunker.flush():
                await chunks.put(piece)
            await chunks.put(None)
            await speaker
        except asyncio.CancelledError:
            cancel.set()
            speaker.cancel()
            try:
                # fermer la connexion : Ollama arrête de générer pour rien
                held["res"].close()
            except Exception:  # noqa: BLE001
                pass
            if reply.strip() and self.audio_sent:
                # coupé en parlant : l'interlocuteur a entendu le début
                self.history.append({"role": "assistant", "content": reply.strip() + " …"})
            raise
        self.history.append({"role": "assistant", "content": reply.strip()})
        marks["total_ms"] = int((time.monotonic() - t0) * 1000)
        print(f"[{self.slug}] {json.dumps(marks, ensure_ascii=False)}", flush=True)
        _log(self.slug, marks, reply)
        await self.send({"type": "fin", "texte": reply.strip(), "mesures": marks})

    async def speak(self, chunks: asyncio.Queue, t0: float, t_last_word: float | None, marks: dict) -> None:
        k = 0
        rtfs = []
        while (piece := await chunks.get()) is not None:
            text = voice_chat.speakable(piece)
            if not text or self.char.get("ref") is None:
                continue
            if hasattr(self.engine, "clone_stream") and config.setting("voice_stream", "1") != "0":
                rtfs.append(await self._speak_stream(text, k, t0, t_last_word, marks))
                k += 1
                continue
            x, sr, gen = await asyncio.to_thread(self._clone, text, k)
            dur = len(x) / sr
            rtfs.append(round(gen / dur, 3) if dur else None)
            if "premier_son_ms" not in marks:
                marks["premier_son_ms"] = int((time.monotonic() - t0) * 1000)
                if t_last_word is not None:
                    marks["premier_son_apres_dernier_mot_ms"] = int((time.monotonic() - t_last_word) * 1000)
            now = time.monotonic()
            self.speaking_until = max(now, self.speaking_until) + dur
            pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
            await self.send_audio({"type": "audio", "i": k, "texte": text, "sr": sr, "ms": int(dur * 1000)}, pcm)
            self.audio_sent = True
            k += 1
        marks["morceaux"] = k
        marks["rtf"] = rtfs

    async def _speak_stream(self, text: str, k: int, t0: float, t_last_word: float | None, marks: dict):
        """Un morceau dit en flux : chaque paquet de son part dès qu'il est
        décodé (voice_qwen.clone_stream). Rend le RTF du morceau."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        abort = threading.Event()
        seed = int(self.char.get("seed") or 1) + k

        def run() -> None:
            gen = self.engine.clone_stream(text, self.char["ref"], self.char["ref_text"], seed, fast=True,
                                           abort=abort)
            try:
                for item in gen:
                    loop.call_soon_threadsafe(q.put_nowait, item)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, exc)
            finally:
                gen.close()
                loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=run, daemon=True).start()
        total, elapsed, j = 0.0, 0.0, 0
        try:
            while (item := await q.get()) is not None:
                if isinstance(item, Exception):
                    raise item
                x, sr, elapsed = item
                if "premier_son_ms" not in marks:
                    marks["premier_son_ms"] = int((time.monotonic() - t0) * 1000)
                    if t_last_word is not None:
                        marks["premier_son_apres_dernier_mot_ms"] = int((time.monotonic() - t_last_word) * 1000)
                dur = len(x) / sr
                total += dur
                self.speaking_until = max(time.monotonic(), self.speaking_until) + dur
                pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
                await self.send_audio({"type": "audio", "i": k, "j": j, "texte": text if j == 0 else "", "sr": sr,
                                       "ms": int(dur * 1000)}, pcm)
                self.audio_sent = True
                j += 1
        finally:
            abort.set()
        return round(elapsed / total, 3) if total else None

    def _clone(self, text: str, k: int):
        seed = int(self.char.get("seed") or 1) + k
        if hasattr(self.engine, "clone_pcm"):
            return self.engine.clone_pcm(text, self.char["ref"], self.char["ref_text"], seed, fast=True)
        t = time.monotonic()
        take = self.engine.clone(text, self.char["ref"], self.char["ref_text"], seed)
        x, sr = wav_samples(take.wav)
        return x, sr, time.monotonic() - t


def _log(slug: str, marks: dict, reply: str) -> None:
    """Chaque tour mesuré, une ligne JSON (`voice_log`, défaut voix-mesures.jsonl
    dans le dossier de lancement)."""
    try:
        with open(config.setting("voice_log", "voix-mesures.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "slug": slug, **marks, "reponse": reply[:300]},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── l'application ──────────────────────────────────────────────────

def build_app(engine, stub: bool):
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
    from pydantic import BaseModel

    app = FastAPI(title="Character Factory — voix")

    class Design(BaseModel):
        text: str
        instruct: str
        seed: int | None = None
        language: str = "French"

    class Clone(BaseModel):
        text: str
        ref_b64: str
        ref_text: str = ""
        seed: int | None = None
        fast: bool = False

    class Pair(BaseModel):
        a_b64: str
        b_b64: str

    def wav_response(take) -> Response:
        d = take.duration_s
        return Response(take.wav, media_type="audio/wav", headers={
            "x-seed": str(take.seed), "x-gen-s": f"{take.gen_s:.3f}", "x-duration-s": f"{d:.2f}",
            "x-rtf": f"{take.gen_s / d:.3f}" if d else "-1", "x-engine": take.engine,
            "x-model": str(take.meta.get("model", ""))})

    def seed_of(s):
        import random

        return s if s is not None else random.randint(1, 2 ** 31 - 1)

    @app.get("/health")
    def health():
        ear = {"url": stt_url()}
        if not stub:
            try:
                u = stt_url().replace("ws://", "http://").replace("wss://", "https://").rsplit("/", 1)[0]
                with urllib.request.urlopen(f"{u}/sante", timeout=2) as res:
                    ear.update(json.loads(res.read()))
            except (OSError, ValueError) as exc:
                ear.update(ok=False, error=str(exc))
        return {**engine.health(), "service": "voix", "stub": stub, "oreille": ear,
                "llm": {"url": voice_chat.llm_url(), "model": voice_chat.llm_model(),
                        "stub": voice_chat.llm_is_stub()},
                "studio": studio_url()}

    @app.post("/tts/design")
    def tts_design(d: Design):
        return wav_response(engine.design(d.text, d.instruct, seed_of(d.seed), d.language))

    @app.post("/tts/clone")
    def tts_clone(c: Clone):
        return wav_response(engine.clone(c.text, base64.b64decode(c.ref_b64), c.ref_text, seed_of(c.seed), c.fast))

    class Wav(BaseModel):
        wav_b64: str

    @app.post("/transcribe")
    def transcribe(w: Wav):
        """Relais vers l'oreille (POST /stt du serveur Kyutai) : le texte dit
        dans un WAV. L'oreille ne tient qu'un flux : pendant une conversation
        en direct, on attend au plus 20 s puis on renonce."""
        if stub:
            return {"text": None}
        u = stt_url().replace("ws://", "http://").replace("wss://", "https://").rsplit("/", 1)[0]
        req = urllib.request.Request(f"{u}/stt", data=base64.b64decode(w.wav_b64), method="POST",
                                     headers={"Content-Type": "audio/wav"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return {"text": json.loads(res.read()).get("texte")}
        except (OSError, ValueError) as exc:
            return {"text": None, "error": str(exc)}

    @app.post("/similarity")
    def similarity(p: Pair):
        return {"cosine": engine.similarity(base64.b64decode(p.a_b64), base64.b64decode(p.b_b64))}

    @app.post("/warm")
    def warm():
        if hasattr(engine, "warm"):
            engine.warm()
        return engine.health()

    @app.websocket("/ws/chat")
    async def chat(ws: WebSocket):
        await ws.accept()
        slug = ws.query_params.get("slug", "")
        if not slug:
            await ws.send_text(json.dumps({"type": "erreur", "message": "personnage manquant : ajouter ?slug=<perso> à "
                                                                       "l'adresse de la WebSocket"}))
            await ws.close()
            return
        session = Session(ws, engine, slug, stub)
        try:
            await session.run()
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    # la page de test, et le studio relayé

    @app.get("/")
    def root():
        return RedirectResponse("/voix.html")

    @app.get("/{rel:path}")
    async def static_or_proxy_get(rel: str, request: Request):
        if rel.startswith(("api/", "files/")):
            return await proxy(rel, request)
        if rel in STATIC or any(rel.startswith(d + "/") for d in STATIC_DIRS):
            target = (config.REPO / rel).resolve()
            if target.is_relative_to(config.REPO.resolve()) and target.is_file():
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".js":
                    ctype = "text/javascript"
                return Response(target.read_bytes(), media_type=ctype, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"error": {"message": "introuvable"}}, 404)

    @app.api_route("/api/{rel:path}", methods=["POST", "PUT"])
    async def proxy_write(rel: str, request: Request):
        return await proxy("api/" + rel, request)

    async def proxy(rel: str, request: Request):
        body = await request.body()
        url = f"{studio_url()}/{rel}" + (f"?{request.url.query}" if request.url.query else "")
        req = urllib.request.Request(url, data=body or None, method=request.method,
                                     headers={"Content-Type": request.headers.get("content-type",
                                                                                  "application/json")})
        try:
            res = await asyncio.to_thread(urllib.request.urlopen, req, timeout=900)
        except urllib.error.HTTPError as exc:
            return Response(exc.read(), status_code=exc.code,
                            media_type=exc.headers.get("Content-Type", "application/json"))
        except (urllib.error.URLError, OSError) as exc:
            return JSONResponse({"error": {"message": f"studio injoignable ({studio_url()}) : {exc}"}}, 502)

        def chunks():
            with res:
                while True:
                    block = res.read1(65536) if hasattr(res, "read1") else res.read(65536)
                    if not block:
                        return
                    yield block

        headers = {"Cache-Control": res.headers.get("Cache-Control", "no-cache")}
        return StreamingResponse(chunks(), status_code=res.status,
                                 media_type=res.headers.get("Content-Type", "application/octet-stream"),
                                 headers=headers)

    return app


def serve(*, host: str = "0.0.0.0", port: int = 8770, https_port: int | None = 8771, stub: bool = False,
          warm: bool = True) -> None:
    import uvicorn

    if stub:
        engine = StubEngine()
        engine.gpu = threading.Lock()
        engine.prompt = lambda ref, text, fast=False: None
    else:
        from .voice_qwen import QwenEngine

        engine = QwenEngine()
        if warm:
            # Les modèles se chargent pendant que le service répond déjà :
            # la première demande attendra la fin du chargement (verrou GPU).
            threading.Thread(target=engine.warm, name="chauffe", daemon=True).start()
    app = build_app(engine, stub)
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", ws_max_size=2 ** 24))]
    if https_port:
        from .tls import ensure_cert

        crt, key = ensure_cert()
        servers.append(uvicorn.Server(uvicorn.Config(app, host=host, port=https_port, log_level="warning",
                                                     ssl_certfile=str(crt), ssl_keyfile=str(key),
                                                     ws_max_size=2 ** 24)))
    print(f"service vocal : http://<cette machine>:{port}/"
          + (f" et https://<cette machine>:{https_port}/voix.html" if https_port else ""), flush=True)
    print(f"  moteur : {engine.name}{' (factice)' if stub else ''} ; oreille : {'factice' if stub else stt_url()}",
          flush=True)
    print(f"  modèle de texte : {voice_chat.llm_model()} ({voice_chat.llm_url()}) ; studio : {studio_url()}",
          flush=True)

    async def main():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(main())


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="voice_server", description="Character Factory — le service vocal")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--https", type=int, default=8771)
    ap.add_argument("--hote", default="0.0.0.0")
    ap.add_argument("--factice", action="store_true")
    ap.add_argument("--froid", action="store_true", help="ne charger les modèles qu'à la première demande")
    a = ap.parse_args()
    serve(host=a.hote, port=a.port, https_port=a.https or None, stub=a.factice, warm=not a.froid)


if __name__ == "__main__":
    _main()
