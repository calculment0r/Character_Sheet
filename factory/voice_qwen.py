"""Qwen3-TTS dans le processus du service vocal (Apache 2.0).

Trois modèles de la même famille, chargés au premier appel, gardés
résidents (quelques Go chacun en bf16 ; DGX1 a la place) :

  - VoiceDesign 1.7B : une voix depuis une description, et la consigne
    de jeu d'une réplique (`generate_voice_design`) ;
  - Base (1.7B par défaut, `voice_clone_model` pour le 0.6B, plus
    rapide) : la voix verrouillée clonée depuis sa référence, en mode
    ICL (référence + sa transcription) — la plus fidèle. Le prompt de
    clonage (codes de la référence, empreinte du locuteur) se calcule
    une fois par référence et se garde en mémoire ;
  - l'empreinte de locuteur de la similarité : ECAPA de SpeechBrain
    (`speechbrain/spkrec-ecapa-voxceleb`) s'il est installé, sinon
    l'encodeur de locuteur de Qwen3-TTS Base lui-même (un ECAPA-TDNN
    aussi, mais celui du cloneur : juge et partie, on le dit).

Un seul calcul à la fois sur le GPU (verrou) : les générations d'une
conversation et d'une audition passent l'une après l'autre.

qwen-tts 0.1.1 n'a pas de vraie génération en flux, ni de consigne pour
le clonage : la latence de la conversation vient du découpage du texte
en morceaux courts (voice_chat.Chunker), pas du moteur.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time

import numpy as np

from . import config
from .voice_engine import Take, wav_bytes

DESIGN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
# La conversation en direct clone par le 0.6B : ~25 % plus rapide au banc
# du 27/09 (RTF 0,6 contre 0,8 sur DGX1), même ressemblance ECAPA.
CHAT_CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
ECAPA = "speechbrain/spkrec-ecapa-voxceleb"
TOKENS_PER_S = 12            # codec à 12,5 Hz


def _max_tokens(text: str) -> int:
    # Garde-fou contre une génération qui ne s'arrête pas : le français se
    # dit à ~14 signes par seconde ; on laisse trois fois plus, plus 4 s.
    return int(TOKENS_PER_S * (4 + 3 * len(text) / 14))


class _Stop(Exception):
    pass


class QwenEngine:
    name = "qwen3-tts"

    def __init__(self) -> None:
        import torch

        self.torch = torch
        self.device = config.setting("voice_device", "cuda:0" if torch.cuda.is_available() else "cpu")
        self.design_name = config.setting("voice_design_model", DESIGN_MODEL)
        self.clone_name = config.setting("voice_clone_model", CLONE_MODEL)
        self.chat_clone_name = config.setting("voice_chat_clone_model", CHAT_CLONE_MODEL)
        self._models: dict[str, object] = {}
        self._prompts: dict[str, object] = {}
        self._ecapa = None
        self._ecapa_tried = False
        self.gpu = threading.Lock()
        self.load_s: dict[str, float] = {}

    # chargement

    def _model(self, name: str):
        if name not in self._models:
            from qwen_tts import Qwen3TTSModel

            t = time.monotonic()
            self._models[name] = Qwen3TTSModel.from_pretrained(name, device_map=self.device,
                                                               dtype=self.torch.bfloat16, attn_implementation="sdpa")
            self.load_s[name] = round(time.monotonic() - t, 1)
            print(f"{name} chargé en {self.load_s[name]} s", flush=True)
        return self._models[name]

    def warm(self, design: bool = True) -> None:
        with self.gpu:
            self._model(self.chat_clone_name)
            self._model(self.clone_name)
            if design:
                self._model(self.design_name)

    def health(self) -> dict:
        t = self.torch
        return {"ok": True, "engine": self.name, "device": self.device,
                "loaded": sorted(self._models), "load_s": self.load_s,
                "design_model": self.design_name, "clone_model": self.clone_name,
                "chat_clone_model": self.chat_clone_name,
                "similarity": "ecapa" if self._ecapa else ("qwen-speaker-encoder" if self._ecapa_tried else "?"),
                "gpu_mb": int(t.cuda.memory_allocated() / 1e6) if t.cuda.is_available() else 0}

    def _seed(self, seed: int) -> None:
        self.torch.manual_seed(seed)
        if self.torch.cuda.is_available():
            self.torch.cuda.manual_seed_all(seed)

    @staticmethod
    def _one(wavs, sr) -> tuple[np.ndarray, int]:
        w = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
        if hasattr(w, "cpu"):
            w = w.float().cpu().numpy()
        return np.asarray(w, dtype=np.float32).reshape(-1), int(sr)

    # audition et jeu

    def design_pcm(self, text: str, instruct: str, seed: int, language: str = "French"):
        with self.gpu:
            m = self._model(self.design_name)
            self._seed(seed)
            t = time.monotonic()
            wavs, sr = m.generate_voice_design(text=text, instruct=instruct, language=language,
                                               max_new_tokens=_max_tokens(text))
            return (*self._one(wavs, sr), time.monotonic() - t)

    def design(self, text: str, instruct: str, seed: int, language: str = "French") -> Take:
        x, sr, gen = self.design_pcm(text, instruct, seed, language)
        return Take(wav_bytes(x, sr), seed, self.name, gen, meta={"model": self.design_name})

    # clonage

    def prompt(self, ref: bytes, ref_text: str, fast: bool = False):
        """Le prompt de clonage d'une référence, calculé une fois par modèle."""
        name = self.chat_clone_name if fast else self.clone_name
        key = hashlib.sha1(ref + ref_text.encode() + name.encode()).hexdigest()
        if key not in self._prompts:
            import soundfile as sf

            x, sr = sf.read(io.BytesIO(ref), dtype="float32", always_2d=True)
            m = self._model(name)
            icl = bool(ref_text.strip())
            self._prompts[key] = m.create_voice_clone_prompt(ref_audio=(x.mean(axis=1), sr),
                                                             ref_text=ref_text.strip() or None,
                                                             x_vector_only_mode=not icl)
        return self._prompts[key]

    def clone_pcm(self, text: str, ref: bytes, ref_text: str, seed: int, language: str = "French",
                  fast: bool = False):
        with self.gpu:
            m = self._model(self.chat_clone_name if fast else self.clone_name)
            items = self.prompt(ref, ref_text, fast)
            self._seed(seed)
            t = time.monotonic()
            wavs, sr = m.generate_voice_clone(text=text, language=language, voice_clone_prompt=items,
                                              max_new_tokens=_max_tokens(text))
            return (*self._one(wavs, sr), time.monotonic() - t)

    def clone_stream(self, text: str, ref: bytes, ref_text: str, seed: int, language: str = "French",
                     fast: bool = True, first: int = 6, every: int = 12, context: int = 25,
                     abort: threading.Event | None = None):
        """Le clonage en flux : le son sort par paquets pendant la génération.

        qwen-tts 0.1.1 ne rend le son qu'à la fin. Mais son « talker »
        produit une trame de codes (16 livres, 12,5 par seconde) à chaque
        pas : un crochet sur son `forward` les recueille au fil de l'eau,
        et le décodeur du tokenizer — causal, qui décode déjà par morceaux
        avec 25 trames de contexte à gauche (`chunked_decode`) — rend
        chaque paquet dès qu'il est complet : `first` trames pour le premier
        (~0,5 s de son), `every` ensuite. Rend (échantillons, taux, secondes
        de calcul écoulées) par paquet."""
        import queue as _queue

        torch = self.torch
        with self.gpu:
            m = self._model(self.chat_clone_name if fast else self.clone_name)
            items = self.prompt(ref, ref_text, fast)
            talker = m.model.talker
            eos = m.model.config.talker_config.codec_eos_token_id
            steps: _queue.Queue = _queue.Queue()
            stop = threading.Event()

            def hook(_mod, _args, out):
                if stop.is_set() or (abort is not None and abort.is_set()):
                    raise _Stop()            # le consommateur est parti (coupure) : on arrête la génération
                hs = getattr(out, "hidden_states", None)
                if hs is not None and len(hs) and hs[-1] is not None:
                    steps.put(hs[-1].detach()[0].reshape(-1))

            handle = talker.register_forward_hook(hook)
            error: list[BaseException] = []

            def run():
                try:
                    self._seed(seed)
                    m.generate_voice_clone(text=text, language=language, voice_clone_prompt=items,
                                           max_new_tokens=_max_tokens(text))
                except BaseException as exc:  # noqa: BLE001 — rendu au consommateur
                    error.append(exc)
                finally:
                    steps.put(None)

            t0 = time.monotonic()
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            ref_code = items[0].ref_code
            left = ref_code[-context:].to(m.model.device) if ref_code is not None else None
            done, frames, sent = False, [], 0
            try:
                while not done:
                    code = steps.get()
                    if code is None:
                        done = True
                    elif int(code[0]) == eos:
                        done = True
                    else:
                        frames.append(code)
                    need = first if sent == 0 else every
                    if frames[sent:] and (len(frames) - sent >= need or done):
                        new = torch.stack(frames[sent:])
                        prev = torch.stack(frames[max(0, sent - context):sent]) if sent else left
                        codes = torch.cat([prev, new]) if prev is not None and len(prev) else new
                        with torch.no_grad():
                            wavs, sr = m.model.speech_tokenizer.decode([{"audio_codes": codes}])
                        w = wavs[0]
                        w = w.float().cpu().numpy() if hasattr(w, "cpu") else np.asarray(w, dtype=np.float32)
                        per_frame = len(w) // len(codes)
                        sent = len(frames)
                        yield w[-len(new) * per_frame:].astype(np.float32), int(sr), time.monotonic() - t0
            finally:
                stop.set()
                worker.join()
                handle.remove()
            if error and not isinstance(error[0], _Stop):
                raise error[0]

    def clone(self, text: str, ref: bytes, ref_text: str, seed: int, fast: bool = False) -> Take:
        x, sr, gen = self.clone_pcm(text, ref, ref_text, seed, fast=fast)
        return Take(wav_bytes(x, sr), seed, self.name, gen,
                    meta={"model": self.chat_clone_name if fast else self.clone_name})

    # similarité de timbre

    def _embed(self, data: bytes) -> np.ndarray:
        import soundfile as sf

        x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        if not self._ecapa_tried:
            self._ecapa_tried = True
            try:
                from speechbrain.inference.speaker import EncoderClassifier

                self._ecapa = EncoderClassifier.from_hparams(source=ECAPA, run_opts={"device": self.device},
                                                             savedir=str(config.REPO / ".cache" / "ecapa"))
            except Exception as exc:  # noqa: BLE001 — repli annoncé dans /health
                print(f"ECAPA indisponible ({exc}) : empreinte par l'encodeur de Qwen3-TTS", flush=True)
                self._ecapa = None
        t = self.torch
        if self._ecapa is not None:
            if sr != 16000:
                import librosa

                x = librosa.resample(x, orig_sr=sr, target_sr=16000)
            emb = self._ecapa.encode_batch(t.from_numpy(x)[None].to(self.device))
            return emb.reshape(-1).float().cpu().numpy()
        m = self._model(self.clone_name).model
        target = m.speaker_encoder_sample_rate
        if sr != target:
            import librosa

            x = librosa.resample(x, orig_sr=sr, target_sr=target)
        emb = m.extract_speaker_embedding(audio=x, sr=target)
        return emb.reshape(-1).float().cpu().numpy()

    def similarity(self, a: bytes, b: bytes) -> float:
        with self.gpu:
            ea, eb = self._embed(a), self._embed(b)
        return float(np.dot(ea, eb) / (np.linalg.norm(ea) * np.linalg.norm(eb) + 1e-9))
