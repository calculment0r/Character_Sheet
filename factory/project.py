"""Un personnage = un dossier.

Tout ce que la chaîne produit pour un personnage vit dans son dossier,
sous `projects/<nom>/`, décrit par un seul manifeste `project.json`. Pas
de base, pas de serveur : le disque est l'entrepôt, et le manifeste dit
ce qui a été choisi, validé, verrouillé.

Les règles dures du brief sont tenues ici, pas dans la documentation :

  - le visage ne se verrouille qu'une fois (§3) ;
  - une seule identité MHR par personnage, figée à la première
    validation (§2) ;
  - chaque étage exige que le précédent soit validé : pas de plein pied
    sans visage verrouillé, pas de planche sans plein pied validé, pas
    de vues sans planche validée (§5.6, §6.1).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = "character-factory/project@1"
IDENTITY_SCHEMA = "character-factory/identity@1"


class ChainError(Exception):
    """Une règle de la chaîne refuse l'opération. Le message dit pourquoi
    et quoi faire, il est affiché tel quel."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "perso"


class Project:
    def __init__(self, root: Path, data: dict) -> None:
        self.root = root
        self.data = data

    # ── ouverture ──────────────────────────────────────────────────

    @classmethod
    def create(cls, name: str, *, style: str = "photoreal", identity: dict | None = None,
               notes: list[str] | None = None, root: Path | None = None) -> "Project":
        base = root or config.projects_root()
        folder = base / slugify(name)
        if (folder / "project.json").exists():
            raise ChainError(f"le personnage « {folder.name} » existe déjà : {folder}")
        folder.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": SCHEMA,
            "name": name,
            "slug": folder.name,
            "style": style,
            "created_at": now(),
            "identity": identity or {},
            "notes": notes or [],
            "face": {"prompt": "", "refs": [], "candidates": [], "locked": None},
            "mhr_identity": None,
            "costumes": {},
            "takes": [],
            "timelines": {},
        }
        p = cls(folder, data)
        p.save()
        return p

    @classmethod
    def open(cls, ref: str | Path) -> "Project":
        """Par nom (slug) sous le dossier des projets, ou par chemin."""
        candidates = [Path(ref), config.projects_root() / slugify(str(ref)), config.projects_root() / str(ref)]
        for folder in candidates:
            manifest = folder / "project.json"
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("schema") != SCHEMA:
                    raise ChainError(f"{manifest} n'est pas un manifeste de la Factory")
                return cls(folder.resolve(), data)
        raise ChainError(f"aucun personnage « {ref} » — `./usine nouveau` pour en créer un, "
                         f"`./usine liste` pour voir ceux qui existent")

    def save(self) -> None:
        # Écriture atomique : un manifeste à moitié écrit perdrait tout.
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".project.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, self.root / "project.json")

    # ── chemins ────────────────────────────────────────────────────

    def path(self, rel: str) -> Path:
        return self.root / rel

    def rel(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))

    def dir(self, rel: str) -> Path:
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        return d

    def import_file(self, source: str | Path, folder: str) -> str:
        """Copie une référence dans le dossier du personnage, pour qu'il
        se suffise à lui-même. Rend le chemin relatif."""
        src = Path(source).expanduser()
        if not src.is_file():
            raise ChainError(f"fichier introuvable : {src}")
        dest_dir = self.dir(folder)
        dest = dest_dir / src.name
        n = 1
        while dest.exists() and not _same_file(dest, src):
            dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
            n += 1
        if not dest.exists():
            shutil.copy2(src, dest)
        return self.rel(dest)

    # ── identité ───────────────────────────────────────────────────

    @property
    def sheet(self) -> dict:
        return self.data.get("identity") or {}

    def set_identity(self, payload: dict) -> None:
        """Accepte l'export de l'étage Identité (bouton « Exporter
        l'identité .json ») ou une fiche nue."""
        if payload.get("schema") == IDENTITY_SCHEMA:
            self.data["identity"] = payload.get("sheet", {})
            self.data["notes"] = payload.get("notes", [])
        else:
            self.data["identity"] = {k: v for k, v in payload.items() if isinstance(v, str)}

    def lock_mhr_identity(self, identity: list[float], scales: list[float], source: str) -> None:
        if self.data.get("mhr_identity"):
            raise ChainError("l'identité MHR de ce personnage est déjà figée — une seule par personnage (§2)")
        if len(identity) != 45 or len(scales) != 68:
            raise ChainError("identité MHR attendue : 45 paramètres de forme et 68 d'échelle")
        self.data["mhr_identity"] = {"identity": identity, "scales": scales, "source": source, "locked_at": now()}

    # ── visage ─────────────────────────────────────────────────────

    @property
    def face(self) -> dict:
        return self.data["face"]

    def require_face(self) -> str:
        locked = self.face.get("locked")
        if not locked:
            raise ChainError("le visage n'est pas verrouillé — `./usine visage` puis `./usine visage-ok` (§3)")
        return locked

    def lock_face(self, candidate: str) -> str:
        if self.face.get("locked"):
            raise ChainError("le visage est déjà verrouillé pour ce personnage : il fait autorité sur toute la "
                             "suite et ne change pas d'un costume à l'autre. Pour un autre visage, crée un autre personnage.")
        chosen = self._pick(self.face["candidates"], candidate, "visage")
        dest = self.path("face/locked.png")
        shutil.copy2(self.path(chosen["file"]), dest)
        self.face.update(locked=self.rel(dest), locked_from=chosen["file"], locked_seed=chosen.get("seed"),
                         locked_backend=chosen.get("backend"), locked_at=now())
        if "stub_identity" in chosen:
            self.face["locked_stub_identity"] = chosen["stub_identity"]
        return self.rel(dest)

    # ── costumes ───────────────────────────────────────────────────

    def costume(self, name: str | None) -> tuple[str, dict]:
        costumes = self.data["costumes"]
        if not costumes:
            raise ChainError("aucun costume — `./usine costume <perso> <nom>` pour en créer un (§4)")
        if name is None:
            if len(costumes) > 1:
                raise ChainError(f"plusieurs costumes, précise lequel : {', '.join(costumes)}")
            name = next(iter(costumes))
        key = slugify(name)
        if key not in costumes:
            raise ChainError(f"costume inconnu : {name} (existants : {', '.join(costumes)})")
        return key, costumes[key]

    def add_costume(self, name: str, prompt: str, refs: list[str]) -> str:
        key = slugify(name)
        if key in self.data["costumes"]:
            raise ChainError(f"le costume « {key} » existe déjà")
        self.data["costumes"][key] = {
            "name": name, "prompt": prompt, "refs": refs, "created_at": now(),
            "fullbody": {"candidates": [], "validated": None},
            "sheets": [], "sheet": None,
            "views": {"method": None, "raw": {}, "prepared": {}, "check": None, "delighted": False},
            "meshes": [], "rigs": [],
        }
        return key

    def validate_fullbody(self, costume: str, candidate: str) -> str:
        key, cos = self.costume(costume)
        chosen = self._pick(cos["fullbody"]["candidates"], candidate, "plein pied")
        dest = self.path(f"costumes/{key}/fullbody.png")
        shutil.copy2(self.path(chosen["file"]), dest)
        cos["fullbody"].update(validated=self.rel(dest), validated_from=chosen["file"], validated_at=now())
        # Un nouveau plein pied rend caduque la planche validée sur l'ancien.
        cos["sheet"] = None
        return self.rel(dest)

    def require_fullbody(self, cos: dict) -> str:
        if not cos["fullbody"].get("validated"):
            raise ChainError("aucun plein pied validé pour ce costume — `./usine pleinpied` puis "
                             "`./usine pleinpied-ok` (§4)")
        return cos["fullbody"]["validated"]

    def require_sheet(self, cos: dict) -> dict:
        sid = cos.get("sheet")
        sheet = next((s for s in cos["sheets"] if s["id"] == sid), None)
        if sheet is None:
            raise ChainError("aucune planche validée pour ce costume — `./usine planche` puis `./usine planche-ok` "
                             "(§5.6 : la planche valide le design avant les vues)")
        return sheet

    # ── outils ─────────────────────────────────────────────────────

    def _pick(self, items: list[dict], ref: str, what: str) -> dict:
        """Un candidat désigné par son numéro (1, 2…) ou son fichier."""
        if not items:
            raise ChainError(f"aucun candidat {what} à choisir")
        if ref.isdigit():
            i = int(ref)
            if not 1 <= i <= len(items):
                raise ChainError(f"candidat {what} n° {i} inexistant (1 à {len(items)})")
            return items[i - 1]
        for it in items:
            if it["file"] == ref or Path(it["file"]).name == ref:
                return it
        raise ChainError(f"candidat {what} introuvable : {ref}")

    def next_id(self, prefix: str, existing: list[dict]) -> str:
        return f"{prefix}{len(existing) + 1:03d}"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def list_projects() -> list[Project]:
    root = config.projects_root()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir()):
        if (folder / "project.json").is_file():
            try:
                out.append(Project.open(folder))
            except (ChainError, ValueError):
                continue
    return out
