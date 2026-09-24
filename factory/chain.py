"""Les étages de la chaîne, un par fonction.

Chaque fonction lit le manifeste du personnage, vérifie que l'étage
précédent est validé, produit ses fichiers dans le dossier du
personnage, et range ce qu'elle a fait. Rien ici ne parle à un
serveur : les modèles tournent sur la machine, par le moteur que la
configuration désigne (`config.backend`).
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

from . import config, h3, imaging, prompts
from .project import ChainError, Project, now

ORTHO = ("front", "left", "back", "right")
TOLERANCE_DEG = 5.0


def _report(verbose: bool = True):
    def report(progress: float, message: str) -> None:
        if verbose:
            print(f"    {int(progress * 100):3d} %  {message}", flush=True)
    return report


def identity_seed(p: Project) -> int:
    """Une graine stable par personnage : le factice dessine ainsi la
    même personne d'un étage à l'autre — celle du visage verrouillé dès
    qu'il y en a un."""
    locked = p.face.get("locked_stub_identity")
    return locked if locked is not None else zlib.crc32(p.data["slug"].encode()) % 100000


def _seed(seed: int | None) -> int:
    return seed if seed is not None else h3.new_seed()


# ── visage ─────────────────────────────────────────────────────────

def face(p: Project, *, prompt: str = "", refs: list[str] = (), variants: int = 4,
         seed: int | None = None, report=None) -> list[dict]:
    """Une grille de variantes du portrait neutre. Avec --graine, les
    variantes partent de cette graine : c'est le verrouillage de graine
    du §3, pour relancer en ne changeant que le prompt."""
    if p.face.get("locked"):
        raise ChainError("le visage est déjà verrouillé : plus de nouvelles variantes pour ce personnage")
    report = report or _report()
    imported = [p.import_file(r, "face/refs") for r in refs]
    p.face["refs"] = list(dict.fromkeys(p.face["refs"] + imported))
    if prompt:
        p.face["prompt"] = prompt
    sections = prompts.face(p.sheet, p.data["notes"], has_source=bool(p.face["refs"]), extra=p.face["prompt"],
                            style=p.data["style"])
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        n = len(p.face["candidates"]) + 1
        dest = p.dir("face") / f"cand-{n:03d}.png"
        print(f"  variante {i + 1}/{variants} · graine {s}")
        # Sans source, le factice varie avec la graine : ce sont bien des
        # candidats différents. Avec une source, l'identité est fixée.
        ident = identity_seed(p) + (0 if p.face["refs"] else n)
        out = h3.still("face", sections=sections, refs=[p.path(r) for r in p.face["refs"][:1]], dest=dest,
                       seed=s, report=report, extra={"identity_seed": ident})
        entry = {"file": p.rel(dest), "seed": s, "backend": out.backend, "at": now()}
        if out.backend == "stub":
            entry["stub_identity"] = ident
        p.face["candidates"].append(entry)
        made.append(entry)
        p.save()
    _contact(p, "face/contact.png", [(f"{k + 1} · {c['seed']}", p.path(c["file"]))
                                     for k, c in enumerate(p.face["candidates"])])
    return made


def face_lock(p: Project, candidate: str) -> str:
    locked = p.lock_face(candidate)
    p.save()
    return locked


# ── costumes ───────────────────────────────────────────────────────

def costume_add(p: Project, name: str, *, prompt: str = "", refs: list[str] = ()) -> str:
    key = p.add_costume(name, prompt, [])
    p.data["costumes"][key]["refs"] = [p.import_file(r, f"costumes/{key}/refs") for r in refs]
    p.save()
    return key


def fullbody(p: Project, costume: str | None, *, variants: int = 2, seed: int | None = None,
             prompt: str = "", report=None) -> list[dict]:
    """Le plein pied habillé, le visage verrouillé comme référence (§4)."""
    locked = p.require_face()
    key, cos = p.costume(costume)
    if prompt:
        cos["prompt"] = prompt
    report = report or _report()
    refs = [p.path(locked)] + [p.path(r) for r in cos["refs"]]
    sections = prompts.fullbody(p.sheet, p.data["notes"], garments=len(cos["refs"]), costume_prompt=cos["prompt"],
                                style=p.data["style"])
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        n = len(cos["fullbody"]["candidates"]) + 1
        dest = p.dir(f"costumes/{key}/fullbody") / f"cand-{n:03d}.png"
        print(f"  variante {i + 1}/{variants} · graine {s}")
        out = h3.still("fullbody", sections=sections, refs=refs, dest=dest, seed=s, report=report,
                       extra={"identity_seed": identity_seed(p), "azimuth": 0.0})
        entry = {"file": p.rel(dest), "seed": s, "backend": out.backend, "at": now()}
        cos["fullbody"]["candidates"].append(entry)
        made.append(entry)
        p.save()
    _contact(p, f"costumes/{key}/fullbody/contact.png",
             [(f"{k + 1} · {c['seed']}", p.path(c["file"])) for k, c in enumerate(cos["fullbody"]["candidates"])])
    return made


def fullbody_ok(p: Project, costume: str | None, candidate: str) -> str:
    key, _ = p.costume(costume)
    out = p.validate_fullbody(key, candidate)
    p.save()
    return out


# ── planche ────────────────────────────────────────────────────────

def sheet(p: Project, costume: str | None, *, mask_face: bool = False, ab: bool = False,
          seed: int | None = None, report=None) -> list[dict]:
    """La planche en cinq frames (§5.2). Avec --ab, deux planches de
    même graine, avec et sans disque sur le visage des plein pieds : le
    §5.5 veut que l'astuce se tranche sur pièces, pas a priori."""
    locked = p.require_face()
    key, cos = p.costume(costume)
    body = p.require_fullbody(cos)
    report = report or _report()
    refs = [p.path(locked), p.path(body)] + [p.path(r) for r in cos["refs"]]
    s = _seed(seed)
    made = []
    for mask in ((False, True) if ab else (mask_face,)):
        sid = p.next_id("s", cos["sheets"])
        folder = p.dir(f"costumes/{key}/sheets/{sid}")
        sections = prompts.plate(p.sheet, p.data["notes"], garments=len(cos["refs"]), mask_face=mask,
                                 costume_prompt=cos["prompt"], style=p.data["style"])
        (folder / "prompt.txt").write_text(prompts.to_text(sections), encoding="utf-8")
        print(f"  planche {sid}{' · disque sur le visage' if mask else ''} · graine {s}")
        out = h3.still("sheet", sections=sections, refs=refs, dest=folder / "sheet.png", seed=s, report=report,
                       extra={"identity_seed": identity_seed(p), "mask_face": mask})
        entry = {"id": sid, "file": p.rel(folder / "sheet.png"), "mask_face": mask, "seed": s,
                 "backend": out.backend, "at": now()}
        cos["sheets"].append(entry)
        made.append(entry)
        p.save()
    if ab:
        _contact(p, f"costumes/{key}/sheets/ab-{made[0]['id']}-{made[1]['id']}.png",
                 [(f"{m['id']} · {'disque' if m['mask_face'] else 'sans disque'}", p.path(m["file"])) for m in made],
                 cell=640, cols=2)
    return made


def sheet_ok(p: Project, costume: str | None, sheet_id: str) -> dict:
    key, cos = p.costume(costume)
    chosen = next((s for s in cos["sheets"] if s["id"] == sheet_id), None)
    if chosen is None:
        raise ChainError(f"planche inconnue : {sheet_id} (existantes : {', '.join(s['id'] for s in cos['sheets'])})")
    cos["sheet"] = sheet_id
    cos["sheet_mask_face"] = chosen["mask_face"]
    # De nouvelles références pour les vues : les anciennes ne valent plus.
    cos["views"] = {"method": None, "raw": {}, "prepared": {}, "check": None, "delighted": False}
    p.save()
    return chosen


# ── vues orthogonales ──────────────────────────────────────────────

def views(p: Project, costume: str | None, *, method: str = "per_view", names: list[str] | None = None,
          threequarter: bool = True, seed: int | None = None, report=None) -> dict:
    """Une génération plein cadre par vue (§6.1), le visage, le plein
    pied et la planche validée en références. `orbit` fait l'autre
    méthode : un seul plan en orbite, redécoupé."""
    locked = p.require_face()
    key, cos = p.costume(costume)
    body = p.require_fullbody(cos)
    plate = p.require_sheet(cos)
    report = report or _report()
    names = list(names or ORTHO) + (["threequarter"] if threequarter and not names else [])
    refs = [p.path(locked), p.path(body), p.path(plate["file"])]
    folder = p.dir(f"costumes/{key}/views/raw")
    s = _seed(seed)
    v = cos["views"]
    v["method"] = method
    if method == "orbit":
        azimuths = {n: prompts.AZIMUTHS[n][0] for n in names}
        sections = prompts.orbit(p.sheet, p.data["notes"], costume_prompt=cos["prompt"], style=p.data["style"])
        print(f"  orbite · {len(names)} azimuts à extraire · graine {s}")
        stills = h3.orbit(sections=sections, refs=refs, dest_dir=folder, seed=s, azimuths=azimuths,
                          report=report, identity_seed=identity_seed(p))
        for n, st in stills.items():
            v["raw"][n] = {"file": p.rel(st.path), "azimuth": azimuths[n], "azimuth_source": "supposé (orbite)",
                           "seed": s, "backend": st.backend, "at": now()}
    else:
        for n in names:
            sections = prompts.view(p.sheet, p.data["notes"], name=n, costume_prompt=cos["prompt"],
                                    style=p.data["style"])
            az = prompts.AZIMUTHS[n][0]
            print(f"  vue {n} · {az:g}° · graine {s}")
            st = h3.still("view", sections=sections, refs=refs, dest=folder / f"{n}.png", seed=s, report=report,
                          extra={"identity_seed": identity_seed(p), "azimuth": az})
            v["raw"][n] = {"file": p.rel(st.path), "azimuth": az, "azimuth_source": "demandé", "seed": s,
                           "backend": st.backend, "at": now()}
    # Des vues neuves invalident la préparation et le contrôle.
    v["prepared"], v["check"], v["delighted"] = {}, None, False
    p.save()
    _contact(p, f"costumes/{key}/views/contact_raw.png",
             [(f"{n} · {e['azimuth']:g}°", p.path(e["file"])) for n, e in v["raw"].items()], cols=5)
    return v["raw"]


def prep(p: Project, costume: str | None, *, delight: bool | None = None, size: int = 1024,
         margin: float = 0.08, report=None) -> dict:
    """La passe de contrôle du §6.2 et le §6.3 : détourage, delight,
    recentrage, même échelle, même marge. Rend le rapport chiffré."""
    key, cos = p.costume(costume)
    v = cos["views"]
    missing = [n for n in ORTHO if n not in v["raw"]]
    if missing:
        raise ChainError(f"vues manquantes : {', '.join(missing)} — `./usine vues` d'abord")
    report = report or _report()
    engine = config.backend("prep")
    use_delight = config.backend("delight") != "off" if delight is None else delight
    if use_delight and config.backend("delight") == "off":
        raise ChainError("delight demandé mais aucun moteur : FACTORY_DELIGHT=hunyuan")

    images = {}
    for i, (n, e) in enumerate(v["raw"].items()):
        report((i + 0.5) / (len(v["raw"]) + 1), f"détourage {n} ({engine})")
        img = imaging.load(p.path(e["file"]))
        if use_delight:
            from . import delight as delight_mod

            img = delight_mod.run(img)
        images[n] = imaging.matte(img, engine)

    out, rep = imaging.normalize_views(images, size=size, margin=margin)
    folder = p.dir(f"costumes/{key}/views/prepared")
    for n, img in out.items():
        img.save(folder / f"{n}.png")
        v["prepared"][n] = {"file": p.rel(folder / f"{n}.png"), "metrics": rep["views"][n]}
    v["delighted"] = bool(use_delight)
    v["prep"] = {**rep["summary"], "matte": engine, "delight": bool(use_delight), "at": now()}
    (folder / "prep.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    v["check"] = None
    p.save()
    _contact(p, f"costumes/{key}/views/contact_prepared.png",
             [(n, p.path(e["file"])) for n, e in v["prepared"].items()], cols=5)
    report(1.0, "vues préparées")
    return rep


def angular_error(measured: float, target: float) -> float:
    """Écart signé le plus court, bouclage à 360° compris."""
    return (measured - target + 180.0) % 360.0 - 180.0


def check(p: Project, costume: str | None, *, angles: dict[str, float] | None = None) -> dict:
    """Refus explicite au-delà de ±5° (§6.2). Les angles viennent, par
    ordre de préférence : de la ligne de commande, d'une mesure, ou de
    ce qui a été demandé au générateur — et le rapport dit lequel."""
    key, cos = p.costume(costume)
    v = cos["views"]
    if not v["prepared"]:
        raise ChainError("vues non préparées — `./usine prep` d'abord")
    errors, table = {}, {}
    for n in ORTHO:
        target = prompts.AZIMUTHS[n][0]
        raw = v["raw"].get(n, {})
        if angles and n in angles:
            value, source = angles[n], "fourni"
        elif raw.get("azimuth_measured") is not None:
            value, source = raw["azimuth_measured"], "mesuré"
        elif n in v["raw"]:
            value, source = raw["azimuth"], raw.get("azimuth_source", "demandé")
        else:
            errors[n] = "vue absente"
            continue
        delta = angular_error(value, target)
        table[n] = {"target": target, "value": value, "error": round(delta, 2), "source": source}
        if abs(delta) > TOLERANCE_DEG:
            errors[n] = f"écart de {abs(delta):.1f}° — au-delà de la tolérance de {TOLERANCE_DEG:g}°"
    spread = v.get("prep", {}).get("height_spread_before", 0.0)
    result = {"ok": not errors, "errors": errors, "angles": table, "tolerance": TOLERANCE_DEG,
              "height_spread_before": spread, "at": now(),
              "measured": any(t["source"] in ("mesuré", "fourni") for t in table.values())}
    v["check"] = result
    p.save()
    return result


# ── 3D ─────────────────────────────────────────────────────────────

def mesh(p: Project, costume: str | None, *, engine: str | None = None, single_view: bool = False,
         seed: int | None = None, texture: bool = True, report=None) -> dict:
    """Le mesh PBR (§7). En multi-vues, les quatre vues préparées doivent
    avoir passé le contrôle : des entrées mal alignées donnent un
    résultat pire qu'une seule image. En mono-vue, c'est le 3/4 qui part,
    une vue de face donnant souvent un dos plat (§7.2)."""
    from . import mesh as mesh_mod

    key, cos = p.costume(costume)
    v = cos["views"]
    engine = engine or mesh_mod.DEFAULT_ENGINE
    report = report or _report()
    single = None
    if single_view:
        chosen = v["prepared"].get("threequarter") or v["prepared"].get("front")
        if not chosen:
            raise ChainError("aucune vue préparée pour le mode image unique — `./usine vues` puis `./usine prep`")
        single = p.path(chosen["file"])
    else:
        chk = v.get("check")
        if not chk:
            raise ChainError("les vues n'ont pas passé le contrôle d'alignement — `./usine controle` (§6.2)")
        if not chk["ok"]:
            detail = "; ".join(f"{n} : {e}" for n, e in chk["errors"].items())
            raise ChainError(f"contrôle d'alignement en échec ({detail}). Des vues mal alignées donnent un mesh "
                             f"pire qu'une seule image : régénère les vues fautives, ou passe en --une-vue.")
    if engine in mesh_mod.LICENSE_NOTE:
        print(f"  note : {mesh_mod.LICENSE_NOTE[engine]}")

    version = len(cos["meshes"]) + 1
    out_dir = p.dir(f"costumes/{key}/mesh/v{version:03d}")
    views = {n: p.path(v["prepared"][n]["file"]) for n in ORTHO if n in v["prepared"]}
    res = mesh_mod.generate(engine, views=views, out_dir=out_dir, seed=_seed(seed), style=p.data["style"],
                            single_view=single, texture=texture, report=report)
    entry = {"version": version, "engine": engine, "backend": res["backend"], "dir": p.rel(out_dir),
             "glb": p.rel(res["glb"]), "maps": {k: p.rel(path) for k, path in res["maps"].items()},
             "stats": res["stats"], "single_view": single_view,
             "parent_version": cos["meshes"][-1]["version"] if cos["meshes"] else None, "at": now()}
    cos["meshes"].append(entry)
    p.save()
    return entry


def _contact(p: Project, rel: str, items: list[tuple[str, Path]], cell: int = 320, cols: int = 4) -> None:
    images = [(label, imaging.load(path)) for label, path in items if Path(path).exists()]
    if images:
        imaging.contact_sheet(images, cell=cell, cols=cols).save(p.path(rel))
