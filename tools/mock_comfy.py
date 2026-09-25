"""Un faux ComfyUI, pour tester le trajet H3 → ComfyUI sans GPU.

    python3 tools/mock_comfy.py            # écoute sur 8199

Parle les routes dont la chaîne se sert — /system_stats, /object_info,
/upload/image, /prompt, /queue, /history/{id}, /view — et rend, pour
chaque workflow, autant de frames que le gabarit en demande, à la taille
demandée : le mannequin factice vu sous l'azimut que le prompt décrit.
Une seule frame est nette, les autres sont floutées : la chaîne doit
garder celle-là.

Il refuse un workflow où un {{…}} n'a pas été rempli, ou qui pointe vers
une image jamais envoyée : exactement les erreurs qu'on veut attraper.
"""

from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageFilter  # noqa: E402

from factory import stubs  # noqa: E402

WORDS = {"zero": 0, "forty-five": 45, "ninety": 90, "one hundred and eighty": 180, "two hundred and seventy": 270}
STATE = {"uploads": {}, "history": {}, "files": {}, "prompts": []}
LOCK = threading.Lock()


def _azimuth(text: str) -> float:
    m = re.search(r"camera azimuth ([a-z\- ]+?) degrees", text)
    return float(WORDS.get(m.group(1).strip(), 0)) if m else 0.0


def _render(wf: dict) -> list[bytes]:
    width = height = frames = None
    text = ""
    for node in wf.values():
        inputs = node.get("inputs", {})
        width = inputs.get("width", width)
        height = inputs.get("height", height)
        for k in ("length", "num_frames", "frames"):
            frames = inputs.get(k, frames)
        for k in ("prompt", "text"):
            if isinstance(inputs.get(k), str):
                text = inputs[k]
    width, height, frames = int(width or 768), int(height or 768), int(frames or 5)
    az = _azimuth(text)
    base = stubs.portrait((width, height), seed=3) if width == height else \
        stubs.mannequin((width, height), azimuth=az, seed=3, label="COMFY FACTICE")
    sharp = frames // 2
    out = []
    for i in range(frames):
        img = base if i == sharp else base.filter(ImageFilter.GaussianBlur(3))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _run(pid: str, wf: dict) -> None:
    time.sleep(0.3)
    frames = _render(wf)
    out_node = next((nid for nid, n in wf.items() if n.get("_meta", {}).get("title") == "OUT"), None) \
        or next(nid for nid, n in wf.items() if n.get("class_type") == "SaveImage")
    images = []
    with LOCK:
        for i, data in enumerate(frames):
            name = f"mock_{pid[:8]}_{i:05d}_.png"
            STATE["files"][name] = data
            images.append({"filename": name, "subfolder": "", "type": "output"})
        STATE["history"][pid] = {"outputs": {out_node: {"images": images}},
                                 "status": {"status_str": "success", "completed": True, "messages": []}}


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/system_stats":
            return self._json({"system": {"comfyui_version": "mock"}, "devices": []})
        if url.path == "/object_info":
            names = ["LoadImage", "SaveImage", "MiniMaxH3ModelLoader", "MiniMaxH3Ref2VA", "H3VAEDecode"]
            return self._json({n: {"input": {}, "output": []} for n in names})
        if url.path == "/queue":
            return self._json({"queue_running": [], "queue_pending": []})
        if url.path.startswith("/history/"):
            pid = url.path.rsplit("/", 1)[1]
            with LOCK:
                return self._json({pid: STATE["history"][pid]} if pid in STATE["history"] else {})
        if url.path == "/view":
            name = parse_qs(url.query).get("filename", [""])[0]
            with LOCK:
                data = STATE["files"].get(name)
            if data is None:
                return self._json({"error": "absent"}, 404)
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if url.path == "/__prompts":
            return self._json(STATE["prompts"])
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n)
        if self.path == "/upload/image":
            m = re.search(rb'filename="([^"]+)"', body)
            name = m.group(1).decode() if m else f"upload_{uuid.uuid4().hex[:6]}.png"
            with LOCK:
                STATE["uploads"][name] = len(body)
            return self._json({"name": name, "subfolder": "", "type": "input"})
        if self.path == "/prompt":
            wf = json.loads(body)["prompt"]
            text = json.dumps(wf)
            if "{{" in text:
                return self._json({"error": "placeholder non rempli", "node_errors": {"?": "{{…}}"}}, 400)
            for nid, node in wf.items():
                img = node.get("inputs", {}).get("image")
                if node.get("class_type") == "LoadImage" and img not in STATE["uploads"]:
                    return self._json({"error": f"image inconnue {img}", "node_errors": {nid: "image"}}, 400)
            pid = uuid.uuid4().hex
            with LOCK:
                STATE["prompts"].append(wf)
            threading.Thread(target=_run, args=(pid, wf), daemon=True).start()
            return self._json({"prompt_id": pid, "number": len(STATE["prompts"]), "node_errors": {}})
        self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8199
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
