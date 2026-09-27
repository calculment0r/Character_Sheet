"""Les moteurs de voix, vus du studio : une même interface, trois façons.

  - `design(text, instruct, seed)` : une voix neuve, depuis une
    description écrite (Qwen3-TTS VoiceDesign) — l'audition, et le jeu
    dirigé d'une réplique ;
  - `clone(text, ref, ref_text, seed)` : la voix verrouillée du
    personnage, depuis sa référence (Qwen3-TTS Base) — la conversation,
    et tout ce qui doit sonner comme lui ;
  - `similarity(a, b)` : la ressemblance de timbre de deux WAV (cosinus
    d'empreintes de locuteur, ECAPA), pour juger qu'une prise jouée est
    encore sa voix.

Moteurs (capacité `voice`) :

  - `stub` : un vrai WAV, une voyelle synthétique dont la hauteur tient
    à la graine (audition) ou à la référence (clone) ; rien d'un
    modèle, et le fichier le dit (`engine: stub`). Sert à tester la
    chaîne, le studio et la page sans GPU ;
  - `remote` : le service vocal (`./usine voix-serveur`, DGX1 par défaut),
    par HTTP, à `voice_url`. Un service qui ne répond pas fait échouer
    l'étage : on ne retombe jamais sur le factice en silence.

Le service vocal a son propre moteur en processus (`voice_qwen.py`), de
même interface ; il rend des WAV mono 24 kHz, 16 bits.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field

from . import config
from .project import ChainError

SR = 24000


@dataclass
class Take:
    wav: bytes
    seed: int
    engine: str
    gen_s: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return wav_duration(self.wav)

    @property
    def rtf(self) -> float | None:
        d = self.duration_s
        return round(self.gen_s / d, 3) if d else None


# ── WAV ────────────────────────────────────────────────────────────

def wav_bytes(samples, sr: int = SR) -> bytes:
    """Échantillons flottants (-1..1) → WAV mono 16 bits."""
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def wav_samples(data: bytes):
    """WAV 16 bits → (échantillons flottants mono, taux)."""
    import numpy as np

    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        raise ChainError(f"WAV {width * 8} bits : 16 bits attendus")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        return 0.0


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:16]


# ── le factice ─────────────────────────────────────────────────────

def _pitch_of(data: bytes) -> float:
    """La hauteur d'un WAV factice (passages par zéro) : un clone factice
    reprend celle de sa référence, pour que la ressemblance ait un sens."""
    import numpy as np

    x, sr = wav_samples(data)
    x = x[: sr * 3]
    if not len(x):
        return 150.0
    crossings = np.count_nonzero(np.diff(np.signbit(x).astype(np.int8)) > 0)
    return max(60.0, min(400.0, crossings / (len(x) / sr)))


def tone(text: str, pitch: float, seed: int) -> bytes:
    """Une voyelle qui « parle » : une syllabe tous les 180 ms environ,
    autant que le texte en compte, la hauteur légèrement modulée."""
    import numpy as np

    rng = np.random.default_rng(seed)
    syll = max(2, len(re_syllables(text)))
    dur = min(20.0, max(0.6, syll * 0.18))
    t = np.arange(int(dur * SR)) / SR
    f = pitch * (1 + 0.04 * np.sin(2 * math.pi * 0.7 * t + rng.uniform(0, 6)))
    phase = 2 * math.pi * np.cumsum(f) / SR
    voice = 0.6 * np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    env = np.clip(np.sin(math.pi * (t * syll / dur) % math.pi), 0, 1) ** 0.6
    fade = np.minimum(1, np.minimum(t, dur - t) / 0.03)
    return wav_bytes(0.3 * voice * env * fade)


def re_syllables(text: str) -> list[str]:
    import re

    return re.findall(r"[aeiouyàâäéèêëîïôöùûüœæ]+", text.lower())


class StubEngine:
    name = "stub"

    def health(self) -> dict:
        return {"ok": True, "engine": "stub", "note": "moteur factice : voyelles synthétiques, aucun modèle"}

    def design(self, text: str, instruct: str, seed: int, language: str = "French") -> Take:
        h = int(hashlib.sha1(f"{instruct}|{seed}".encode()).hexdigest()[:6], 16)
        return Take(tone(text, 95 + h % 160, seed), seed, "stub", meta={"model": "stub-design"})

    def clone(self, text: str, ref: bytes, ref_text: str, seed: int, fast: bool = False) -> Take:
        return Take(tone(text, _pitch_of(ref), seed), seed, "stub", meta={"model": "stub-clone"})

    def transcribe(self, wav: bytes) -> str | None:
        return None

    def similarity(self, a: bytes, b: bytes) -> float | None:
        pa, pb = _pitch_of(a), _pitch_of(b)
        return round(math.exp(-abs(pa - pb) / 40.0), 3)


# ── le service vocal ───────────────────────────────────────────────

def service_url() -> str:
    return config.setting("voice_url", "http://127.0.0.1:8770").rstrip("/")


class RemoteEngine:
    """Le service vocal, par HTTP : JSON en entrée, WAV en sortie, les
    mesures dans les en-têtes (comme les adaptateurs du Voice Lab)."""

    name = "remote"

    def __init__(self, url: str | None = None, timeout: float = 600) -> None:
        self.url = (url or service_url()).rstrip("/")
        self.timeout = timeout

    def _call(self, path: str, body: dict) -> tuple[bytes, dict]:
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.read(), {k.lower(): v for k, v in res.headers.items()}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise ChainError(f"service vocal {self.url}{path} : {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ChainError(f"service vocal injoignable ({self.url}) : {exc} — `./usine voix-serveur` sur la "
                             f"machine de la voix, ou FACTORY_VOICE=stub") from exc

    def _take(self, raw: bytes, headers: dict, seed: int) -> Take:
        return Take(raw, int(headers.get("x-seed", seed)), headers.get("x-engine", "remote"),
                    float(headers.get("x-gen-s", 0) or 0),
                    meta={"model": headers.get("x-model", ""), "rtf": headers.get("x-rtf")})

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=3) as res:
                return json.loads(res.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def design(self, text: str, instruct: str, seed: int, language: str = "French") -> Take:
        raw, h = self._call("/tts/design", {"text": text, "instruct": instruct, "seed": seed, "language": language})
        return self._take(raw, h, seed)

    def clone(self, text: str, ref: bytes, ref_text: str, seed: int, fast: bool = False) -> Take:
        raw, h = self._call("/tts/clone", {"text": text, "ref_b64": base64.b64encode(ref).decode(),
                                           "ref_text": ref_text, "seed": seed, "fast": fast})
        return self._take(raw, h, seed)

    def transcribe(self, wav: bytes) -> str | None:
        """Ce qui est dit dans un WAV (Kyutai STT, par le service) ; None si
        l'oreille est occupée ou absente : l'appelant garde son texte."""
        try:
            raw, _ = RemoteEngine(self.url, timeout=30)._call("/transcribe", {"wav_b64": base64.b64encode(wav).decode()})
            return (json.loads(raw).get("text") or "").strip() or None
        except (ChainError, ValueError):
            return None

    def similarity(self, a: bytes, b: bytes) -> float | None:
        raw, _ = self._call("/similarity", {"a_b64": base64.b64encode(a).decode(),
                                            "b_b64": base64.b64encode(b).decode()})
        value = json.loads(raw).get("cosine")
        return None if value is None else round(float(value), 3)


def engine():
    return RemoteEngine() if config.backend("voice") == "remote" else StubEngine()
