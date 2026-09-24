"""`./usine voir` : ouvre le viewer 3D sur les fichiers d'un costume.

Le viewer est une page statique (`viewer.html`). Ouverte depuis le
disque, elle accepte un .glb déposé à la main ; pour qu'elle charge
d'elle-même une version, il faut la servir en http — un simple serveur
de fichiers sur 127.0.0.1, le temps de regarder, rien d'autre.
"""

from __future__ import annotations

import functools
import http.server
import urllib.parse
import webbrowser
from pathlib import Path

from . import config
from .project import ChainError, Project

PROJECTS_PREFIX = "/_projets/"


def resolve(p: Project, costume: str | None, ref: str | None) -> Path:
    """`2` ou `mesh:2` → le mesh v2 ; `rig:1` → le rig v1 ; `bake:main`
    → la timeline cuite ; rien → le rig le plus récent, sinon le mesh."""
    key, cos = p.costume(costume)
    if ref is None:
        if cos["rigs"]:
            return p.path(cos["rigs"][-1]["glb"])
        if cos["meshes"]:
            return p.path(cos["meshes"][-1]["glb"])
        raise ChainError("aucun mesh pour ce costume — `./usine mesh` d'abord")
    kind, _, value = ref.partition(":") if ":" in ref else ("mesh", "", ref)
    if kind == "bake":
        tl = p.data["timelines"].get(value)
        if not tl or not tl.get("baked"):
            raise ChainError(f"timeline non cuite : {value}")
        return p.path(tl["baked"]["glb"])
    items = cos["meshes"] if kind == "mesh" else cos["rigs"] if kind == "rig" else None
    if items is None:
        raise ChainError(f"référence inconnue : {ref} (mesh:N, rig:N ou bake:nom)")
    found = next((it for it in items if str(it["version"]) == value), None)
    if found is None:
        raise ChainError(f"{kind} v{value} introuvable")
    return p.path(found["glb"])


class _Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if clean.startswith(PROJECTS_PREFIX):
            root = config.projects_root().resolve()
            target = (root / clean[len(PROJECTS_PREFIX):]).resolve()
            return str(target) if target.is_relative_to(root) else str(root / "__refus__")
        return super().translate_path(path)

    def log_message(self, *args) -> None:
        pass


def _url(path: Path) -> str:
    rel = path.resolve().relative_to(config.projects_root().resolve())
    return PROJECTS_PREFIX + urllib.parse.quote(str(rel))


def serve(p: Project, *, costume: str | None, a: str | None, b: str | None, port: int = 8766,
          open_browser: bool = True) -> None:
    query = {"src": _url(resolve(p, costume, a))}
    if b:
        query["b"] = _url(resolve(p, costume, b))
    _run(f"viewer.html?{urllib.parse.urlencode(query)}", port, open_browser, "viewer")


def serve_page(*, port: int = 8765, open_browser: bool = True) -> None:
    """La page (étage Identité) servie en local. Le modèle de texte est
    celui de la machine : son URL se pose dans l'écran MOTEUR."""
    _run("index.html", port, open_browser, "page")


def _run(path: str, port: int, open_browser: bool, label: str) -> None:
    handler = functools.partial(_Handler, directory=str(config.REPO))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/{path}"
    print(f"{label} : {url}")
    print("  Ctrl+C pour arrêter")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
