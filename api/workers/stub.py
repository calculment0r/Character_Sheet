"""Worker factice.

Il ne charge aucun modèle : il fabrique une image de test, la range dans
le stockage et rend son URL. C'est le critère du lot 1 du brief — depuis
un réseau extérieur, lancer un travail et voir la progression — sans
attendre qu'un GPU soit disponible.
"""

from __future__ import annotations

import struct
import tempfile
import time
import zlib
from pathlib import Path

import storage

# Les tons du rack, pour que l'image de test ne détonne pas dans l'UI.
PALETTE = [(0x0a, 0x0d, 0x0b), (0x1b, 0x21, 0x1e), (0x2f, 0x6b, 0x4a), (0xe0, 0x67, 0x4a)]


def _png(width: int, height: int, bands: int = 4) -> bytes:
    """Un PNG minimal écrit à la main, pour ne dépendre d'aucune
    bibliothèque d'image dans le chemin de mise au point."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filtre « none » en tête de chaque ligne
        band = PALETTE[(y * bands // height) % len(PALETTE)]
        rows.extend(bytes(band) * width)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def run(*, report, job_id: str = "", kind: str = "stub",
        width: int = 768, height: int = 1024, steps: int = 6, **_) -> dict:
    report(0.05, f"worker factice — {kind}")

    for i in range(steps):
        time.sleep(0.4)
        report((i + 1) / (steps + 1), f"étape {i + 1}/{steps}")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(_png(width, height))
        tmp = Path(fh.name)

    try:
        key = storage.put(tmp, key=f"{kind}/{job_id or 'test'}.png")
    finally:
        tmp.unlink(missing_ok=True)

    report(0.98, "rangement")
    return {"url": storage.url_for(key), "key": key, "width": width, "height": height}
