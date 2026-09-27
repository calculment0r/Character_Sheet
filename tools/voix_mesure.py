"""Mesurer la conversation en direct sans micro.

    python tools/voix_mesure.py ws://127.0.0.1:8770/ws/chat <perso> question1.wav [question2.wav …]

Joue chaque WAV dans la WebSocket du service vocal comme le ferait un
micro (PCM 16 bits, 24 kHz, paquets de 80 ms, en temps réel), puis du
silence, et mesure depuis la **fin de la parole** (dernier échantillon
au-dessus du seuil, pas la fin du fichier) : la fin de tour de l'oreille,
le premier mot du modèle de texte, le premier son rendu. Écrit les
réponses reçues à côté (`<question>.reponse.wav`). Besoin : numpy,
soundfile, websockets (l'environnement du service vocal les a).
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

SR = 24000
FRAME = 1920          # 80 ms
CRAN = os.environ.get("CRAN", "normal")   # patience de la fin de tour : vif, normal, patient


def load(path: str) -> tuple[np.ndarray, float]:
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        import librosa

        x = librosa.resample(x, orig_sr=sr, target_sr=SR)
    loud = np.flatnonzero(np.abs(x) > 0.02)
    end = (loud[-1] + 1) / SR if len(loud) else len(x) / SR
    return x, end


async def one(ws, path: str) -> dict:
    x, speech_end = load(path)
    x = np.concatenate([x, np.zeros(int(0.1 * SR), np.float32)])
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    t_start = time.monotonic()
    marks: dict = {"question": Path(path).name}
    audio: list[bytes] = []
    sr_out = SR
    got_first_audio = asyncio.Event()
    done = asyncio.Event()

    async def reader():
        nonlocal sr_out
        while not done.is_set():
            m = await ws.recv()
            now = time.monotonic() - t_start - speech_end
            if isinstance(m, bytes):
                audio.append(m)
                if not got_first_audio.is_set():
                    marks["premier_son_s"] = round(now, 3)
                    got_first_audio.set()
                continue
            m = json.loads(m)
            kind = m["type"]
            if kind == "mot":
                marks.setdefault("premier_mot_reconnu_s", round(now, 3))
                marks["dernier_mot_reconnu_s"] = round(now, 3)
            elif kind == "tour":
                marks.setdefault("fin_de_tour_s", round(now, 3))
                marks["entendu"] = (marks.get("entendu", "") + " | " + m["texte"]).strip(" |")
            elif kind == "stop":
                marks.setdefault("coupures", []).append(m.get("raison"))
            elif kind == "texte":
                marks.setdefault("premier_mot_modele_s", round(now, 3))
            elif kind == "audio":
                sr_out = m["sr"]
            elif kind == "fin":
                marks["reponse"] = m["texte"]
                marks["serveur"] = m["mesures"]
                done.set()
            elif kind == "erreur":
                marks.setdefault("erreurs", []).append(m["message"])

    task = asyncio.create_task(reader())
    for i in range(0, len(pcm), FRAME):
        target = t_start + i / SR
        await asyncio.sleep(max(0.0, target - time.monotonic()))
        await ws.send(pcm[i:i + FRAME].tobytes())
    silence = np.zeros(FRAME, "<i2").tobytes()
    t_sil = time.monotonic()
    while not done.is_set() and time.monotonic() - t_sil < 60:
        await ws.send(silence)
        await asyncio.sleep(FRAME / SR)
    task.cancel()
    if audio:
        out = np.frombuffer(b"".join(audio), dtype="<i2")
        sf.write(str(Path(path).with_suffix(".reponse.wav")), out, sr_out)
    await ws.send(json.dumps({"type": "lecture_finie"}))
    return marks


async def main(url: str, slug: str, paths: list[str]) -> None:
    async with websockets.connect(f"{url}?slug={slug}", max_size=None) as ws:
        while json.loads(await ws.recv())["type"] != "pret":
            pass
        await ws.send(json.dumps({"type": "micro", "on": True, "cran": CRAN}))
        while True:
            m = await ws.recv()
            if isinstance(m, str) and json.loads(m)["type"] == "ecoute":
                break
        for path in paths:
            marks = await one(ws, path)
            print(json.dumps(marks, ensure_ascii=False), flush=True)
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
