"""Faux DGX en dialecte OpenAI — sert à vérifier la traduction de llm.js."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TURNS = [
    # 1er tour : le modèle range deux champs puis pose un widget.
    {"tool_calls": [
        {"id": "c1", "type": "function", "function": {
            "name": "update_character_sheet",
            "arguments": json.dumps({"character_name": "Vera Solen", "role": "pilote de fret orbital"})}},
        {"id": "c2", "type": "function", "function": {
            "name": "add_note", "arguments": json.dumps({"text": "col toujours relevé"})}},
    ], "content": "Je pose les bases."},
    # 2e tour : un widget à chips, qui doit interrompre la boucle.
    {"tool_calls": [
        {"id": "c3", "type": "function", "function": {
            "name": "request_input",
            "arguments": json.dumps({"field": "archetype", "question": "Quel archétype ?",
                                     "input_type": "chips", "options": ["Vétérane", "Franc-tireuse", "Mentor"],
                                     "allow_custom": True})}},
    ], "content": ""},
    {"tool_calls": [], "content": "Noté, la fiche avance."},
]

class H(BaseHTTPRequestHandler):
    calls = 0
    seen = []

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "*")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "*")
        self.send_header("access-control-allow-methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._send({"data": [{"id": "qwen3-vl-32b"}, {"id": "llama-3-70b"}]})
        if self.path == "/__seen":
            return self._send(H.seen)
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        H.seen.append(payload)
        turn = TURNS[min(H.calls, len(TURNS) - 1)]
        H.calls += 1
        self._send({
            "id": "mock", "object": "chat.completion", "model": payload.get("model"),
            "choices": [{"index": 0, "finish_reason": "tool_calls" if turn["tool_calls"] else "stop",
                         "message": {"role": "assistant", "content": turn["content"],
                                     **({"tool_calls": turn["tool_calls"]} if turn["tool_calls"] else {})}}],
        })

    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8812), H).serve_forever()
