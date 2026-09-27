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

from . import config, h3, imaging, prompts, views_qwen
from .project import ChainError, Project, apose as apose_of, now

ORTHO = ("front", "left", "back", "right")
TOLERANCE_DEG = 5.0
VIEW_TRIES = 3   # graines essayées par vue quand l'angle mesuré s'écarte


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
         seed: int | None = None, engine: str | None = None, descriptions: list[str] | None = None,
         report=None) -> list[dict]:
    """Une grille de variantes du portrait neutre. Avec --graine, les
    variantes partent de cette graine : c'est le verrouillage de graine
    du §3, pour relancer en ne changeant que le prompt.

    Le moteur : H3 avec une photo source (il la normalise en gardant la
    personne), sinon un modèle d'image (`portrait.py`, Z-Image Turbo par
    défaut) — le §3 laisse le choix, et H3 rend mal un portrait fixe.
    La description du visage vient du brief lu par le modèle de texte
    (`face.prompt_en`), sinon des précisions tapées (`face.prompt`) ;
    `descriptions` en donne une par variante, pour explorer."""
    from . import portrait

    if p.face.get("locked"):
        raise ChainError("le visage est déjà verrouillé : plus de nouvelles variantes pour ce personnage")
    report = report or _report()
    imported = [p.import_file(r, "face/refs") for r in refs]
    p.face["refs"] = list(dict.fromkeys(p.face["refs"] + imported))
    if prompt:
        p.face["prompt"] = prompt
    engine = engine or ("h3" if p.face["refs"] else config.setting("face_engine", "zimage"))
    if engine not in portrait.ENGINES:
        raise ChainError(f"moteur de visage inconnu : {engine} (possibles : {', '.join(portrait.ENGINES)})")
    p.face["engine"] = engine
    extras = [d for d in descriptions or [] if d] or [p.face.get("prompt_en") or p.face["prompt"]]
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        n = len(p.face["candidates"]) + 1
        dest = p.dir("face") / f"cand-{n:03d}.png"
        print(f"  variante {i + 1}/{variants} · {portrait.ENGINES[engine]} · graine {s}")
        # Sans source, le factice varie avec la graine : ce sont bien des
        # candidats différents. Avec une source, l'identité est fixée.
        ident = identity_seed(p) + (0 if p.face["refs"] else n)
        extra = extras[i % len(extras)]
        if engine == "h3":
            sections = prompts.face(p.sheet, p.data["notes"], has_source=bool(p.face["refs"]), extra=extra,
                                    style=p.data["style"])
            out = h3.still("face", sections=sections, refs=[p.path(r) for r in p.face["refs"][:1]], dest=dest,
                           seed=s, report=report, extra={"identity_seed": ident})
            backend = out.backend
        else:
            text = portrait.text(p.sheet, extra, style=p.data["style"])
            portrait.generate(engine, prompt=text, dest=dest, seed=s, report=report, identity_seed=ident)
            backend = config.backend("portrait")
            dest.with_suffix(".json").write_text(json.dumps(
                {"kind": "face", "engine": engine, "backend": backend, "seed": s, "prompt": text},
                ensure_ascii=False, indent=2), encoding="utf-8")
        entry = {"file": p.rel(dest), "seed": s, "backend": backend, "engine": engine, "desc": extra, "at": now()}
        if backend == "stub":
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
             prompt: str = "", engine: str | None = None, report=None) -> list[dict]:
    """Le plein pied habillé, le visage verrouillé comme référence (§4),
    en pose naturelle : l'A-pose vient ensuite, par `apose`.

    Par Qwen-Image 2.1 turbo en HD (`figure.py`, `qwen21.py`) : H3 le
    rendait à 768 px, mains et matières comprises, trop pauvre pour une
    image qui sert ensuite de référence à l'A-pose et aux vues."""
    from . import figure

    locked = p.require_face()
    key, cos = p.costume(costume)
    if prompt:
        cos["prompt"] = prompt
    engine = engine or config.setting("fullbody_engine", "qwen21")
    if engine not in figure.ENGINES:
        raise ChainError(f"moteur de plein pied inconnu : {engine} (possibles : {', '.join(figure.ENGINES)})")
    report = report or _report()
    refs = [p.path(locked)] + [p.path(r) for r in cos["refs"]]
    if engine == "h3":
        sections = prompts.fullbody(p.sheet, p.data["notes"], garments=len(cos["refs"]),
                                    costume_prompt=cos["prompt"], style=p.data["style"])
    else:
        outfit = prompts.describe_outfit(p.sheet, cos["prompt"])
        text = figure.text(outfit, garments=len(cos["refs"]), style=p.data["style"], tags=engine == "qwen21")
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        n = len(cos["fullbody"]["candidates"]) + 1
        dest = p.dir(f"costumes/{key}/fullbody") / f"cand-{n:03d}.png"
        print(f"  variante {i + 1}/{variants} · {figure.ENGINES[engine]} · graine {s}")
        if engine == "h3":
            out = h3.still("fullbody", sections=sections, refs=refs, dest=dest, seed=s, report=report,
                           extra={"identity_seed": identity_seed(p), "azimuth": 0.0})
            backend = out.backend
        else:
            figure.generate(engine, prompt=text, refs=refs, dest=dest, seed=s, report=report,
                            identity_seed=identity_seed(p))
            backend = config.backend("portrait")
            dest.with_suffix(".json").write_text(json.dumps(
                {"kind": "fullbody", "engine": engine, "backend": backend, "seed": s, "prompt": text,
                 "refs": [str(r) for r in refs]}, ensure_ascii=False, indent=2), encoding="utf-8")
        entry = {"file": p.rel(dest), "seed": s, "backend": backend, "engine": engine, "at": now()}
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


# ── A-pose ─────────────────────────────────────────────────────────

def apose(p: Project, costume: str | None, *, variants: int = 2, seed: int | None = None, report=None) -> list[dict]:
    """Le plein pied validé remis en A-pose (`pose.py`) : un squelette
    relevé sur lui par DWPose, bras à 45°, donné à Qwen-Image 2.1 turbo
    avec le plein pied. Les vues et le mesh partent de là."""
    from . import pose, qwen21
    from . import stubs as sketches

    p.require_face()
    key, cos = p.costume(costume)
    body = p.require_fullbody(cos)
    report = report or _report()
    state = apose_of(cos)
    folder = p.dir(f"costumes/{key}/apose")
    skel = pose.skeletons(p.path(body), folder / "skeleton.png", report=report)[0]
    state["skeleton"] = p.rel(skel)
    text = pose.text_apose(prompts.describe_outfit(p.sheet, cos["prompt"]), p.data["style"])
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        dest = folder / f"cand-{len(state['candidates']) + 1:03d}.png"
        print(f"  A-pose {i + 1}/{variants} · Qwen-Image 2.1 turbo · graine {s}")
        qwen21.generate(prompt=text, refs=[p.path(body), skel], dest=dest, seed=s, size=pose.SIZE,
                        resolution=pose.RESOLUTION, report=report,
                        stub=lambda: sketches.mannequin(pose.SIZE, azimuth=0.0, seed=identity_seed(p)))
        backend = config.backend("portrait")
        dest.with_suffix(".json").write_text(json.dumps(
            {"kind": "apose", "backend": backend, "seed": s, "prompt": text, "refs": [body, p.rel(skel)]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        entry = {"file": p.rel(dest), "seed": s, "backend": backend, "engine": "qwen21", "at": now()}
        state["candidates"].append(entry)
        made.append(entry)
        p.save()
    return made


def apose_ok(p: Project, costume: str | None, candidate: str) -> str:
    key, _ = p.costume(costume)
    out = p.validate_apose(key, candidate)
    p.save()
    return out


# ── planche ────────────────────────────────────────────────────────

def sheet(p: Project, costume: str | None, *, engine: str = "qwen21", variants: int = 1, mask_face: bool = False,
          ab: bool = False, seed: int | None = None, report=None) -> list[dict]:
    """La planche de référence.

      qwen21  par défaut : trois cases — face et dos en pied en A-pose, gros
              plan tête et épaules — par Qwen-Image 2.1 turbo, le visage en
              <image1>, l'A-pose validée en <image2>, une mise en page faite
              des squelettes en <image3> (`pose.py`). Elle se fait après
              l'A-pose et sert à Cal comme au turnaround H3 de la fin ;
      h3      l'ancienne planche H3 en cinq frames (§5.2). Avec --ab, deux
              planches de même graine, avec et sans disque sur le visage.

    Aucune n'ouvre ni ne ferme les vues (Cal, 25/09)."""
    if engine == "qwen21":
        return _sheet_qwen21(p, costume, variants=variants, seed=seed, report=report)
    if engine != "h3":
        raise ChainError(f"moteur de planche inconnu : {engine} (possibles : qwen21, h3)")
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


def _sheet_qwen21(p: Project, costume: str | None, *, variants: int, seed: int | None, report) -> list[dict]:
    from . import pose, qwen21
    from . import stubs as sketches

    locked = p.require_face()
    key, cos = p.costume(costume)
    p.require_fullbody(cos)
    body = p.require_apose(cos)
    report = report or _report()
    sid = p.next_id("s", cos["sheets"])
    folder = p.dir(f"costumes/{key}/sheets/{sid}")
    skel = pose.skeletons(p.path(body), folder / "skeleton.png", [180], report=report)
    layout = pose.sheet_layout(skel[0], skel[180], folder / "layout.png")
    head = pose.head_only(p.path(locked), folder / "face.png")
    text = pose.text_sheet(p.data["style"])
    (folder / "prompt.txt").write_text(text, encoding="utf-8")
    base = _seed(seed)
    made = []
    for i in range(variants):
        s = base + i
        if i:
            sid = p.next_id("s", cos["sheets"])
            p.dir(f"costumes/{key}/sheets/{sid}")
        dest = p.path(f"costumes/{key}/sheets/{sid}/sheet.png")
        print(f"  planche {sid} · Qwen-Image 2.1 turbo · graine {s}")
        qwen21.generate(prompt=text, refs=[head, p.path(body), layout], dest=dest, seed=s, size=pose.SHEET_SIZE,
                        resolution=pose.RESOLUTION, report=report,
                        stub=lambda: sketches.mannequin(pose.SHEET_SIZE, azimuth=0.0, seed=identity_seed(p)))
        entry = {"id": sid, "file": p.rel(dest), "engine": "qwen21", "mask_face": False, "seed": s,
                 "backend": config.backend("portrait"), "layout": p.rel(layout), "at": now()}
        cos["sheets"].append(entry)
        made.append(entry)
        p.save()
    return made


def sheet_ok(p: Project, costume: str | None, sheet_id: str) -> dict:
    """Retenir une planche H3. Elle n'ouvre plus les vues (Cal, 25/09) :
    les méthodes H3 des vues la prennent en référence si elle existe."""
    key, cos = p.costume(costume)
    chosen = next((s for s in cos["sheets"] if s["id"] == sheet_id), None)
    if chosen is None:
        raise ChainError(f"planche inconnue : {sheet_id} (existantes : {', '.join(s['id'] for s in cos['sheets'])})")
    cos["sheet"] = sheet_id
    cos["sheet_mask_face"] = chosen["mask_face"]
    p.save()
    return chosen


# ── vues orthogonales ──────────────────────────────────────────────

VIEW_METHODS = ("qwen21-pose", "per_view", "orbit", *views_qwen.METHODS)


def views(p: Project, costume: str | None, *, method: str = "qwen21-pose", names: list[str] | None = None,
          threequarter: bool = True, seed: int | None = None, bench: bool = False, report=None) -> dict:
    """Les vues orthogonales (§6.1), depuis l'A-pose validée, par l'une
    des méthodes :

      qwen21-pose   par défaut : une édition Qwen-Image 2.1 turbo par vue,
                    l'A-pose en <image1> et le squelette A-pose tourné à
                    l'azimut de la vue en <image2> (`pose.py`) — profils,
                    dos, même échelle et même ligne de sol tenus ; la face
                    est l'A-pose elle-même ;
      per_view      une génération H3 par vue — H3 y revient vers la face ;
      orbit         un plan H3 en orbite, frames choisies sur la silhouette ;
      qwen21-orbit, qwen-2511, qwen-2509
                    l'A-pose tournée par un LoRA d'angle Qwen
                    (`views_qwen.py`, voir les réserves de `docs/ETUDES.md`).

    Avec `bench`, les vues vont dans `views/banc/<méthode>/`, avec leur
    planche contact, et le manifeste n'est pas touché : c'est le banc du
    §6.1, pour comparer les méthodes sur un même costume."""
    if method not in VIEW_METHODS:
        raise ChainError(f"méthode de vues inconnue : {method} (possibles : {', '.join(VIEW_METHODS)})")
    locked = p.require_face()
    key, cos = p.costume(costume)
    p.require_fullbody(cos)
    body = p.require_apose(cos)
    report = report or _report()
    names = list(names or ORTHO) + (["threequarter"] if threequarter and not names else [])
    plate = next((x for x in cos["sheets"] if x["id"] == cos.get("sheet")), None)
    refs = [p.path(locked), p.path(body)] + ([p.path(plate["file"])] if plate else [])
    folder = p.dir(f"costumes/{key}/views/" + (f"banc/{method}" if bench else "raw"))
    s = _seed(seed)
    raw: dict[str, dict] = {}
    if method == "qwen21-pose":
        from . import pose, qwen21
        from . import stubs as sketches

        turns = {n: int(prompts.AZIMUTHS[n][0]) for n in names if n != "front"}
        skel = pose.skeletons(p.path(body), folder / "skeleton.png", sorted(set(turns.values())), report=report)
        # Mesurer puis choisir : chaque vue passe par SAM 3D Body dès qu'elle
        # sort, et se relance sur une autre graine tant qu'elle s'écarte de
        # l'angle voulu (Qwen tient les profils et le dos, pas toujours les
        # 3/4 : essais du 25/09, `docs/ETUDES.md`). On garde la plus proche.
        measure = config.backend("portrait") != "stub"
        workdir = p.dir(f"costumes/{key}/views/.sam3d")
        front_yaw = None
        if measure:
            from . import sam3d

            report(0.05, "SAM 3D Body · face")
            front_yaw = sam3d.yaw(p.path(body), workdir=workdir, name="front")
        for n in names:
            dest = folder / f"{n}.png"
            if n == "front":
                imaging.load(p.path(body)).save(dest)
                raw[n] = {"file": p.rel(dest), "azimuth": 0.0, "azimuth_source": "A-pose validée", "seed": None,
                          "backend": "reprise", "at": now(),
                          **({"azimuth_measured": 0.0} if measure else {})}
                continue
            az = turns[n]
            limit = TOLERANCE_DEG if n in ORTHO else 2 * TOLERANCE_DEG
            text = pose.text_view(az, p.data["style"])
            tries: list[dict] = []
            for k in range(VIEW_TRIES if measure else 1):
                seed_k = s + k
                out = folder / (f"{n}.png" if k == 0 else f".{n}_{k}.png")
                print(f"  vue {n} · {az}° · squelette · graine {seed_k}")
                qwen21.generate(prompt=text, refs=[p.path(body), skel[az]], dest=out, seed=seed_k, size=pose.SIZE,
                                resolution=pose.RESOLUTION, report=report,
                                stub=lambda: sketches.mannequin(pose.SIZE, azimuth=float(az), seed=identity_seed(p)))
                got = {"file": out, "seed": seed_k}
                if measure:
                    got["yaw"] = sam3d.yaw(out, workdir=workdir, name=f"{n}_{k}")
                    got["azimuth"] = sam3d.azimuth(got["yaw"], front_yaw)
                    got["error"] = angular_error(got["azimuth"], az)
                    print(f"    mesuré {got['azimuth']:.1f}° (écart {got['error']:+.1f}°)")
                tries.append(got)
                if not measure or abs(got["error"]) <= limit:
                    break
            best = min(tries, key=lambda t: abs(t.get("error", 0.0)))
            if best["file"] != dest:
                best["file"].replace(dest)
            for t in tries:
                if t["file"] != dest and t["file"].exists():
                    t["file"].unlink()
            backend = config.backend("portrait")
            entry = {"file": p.rel(dest), "azimuth": float(az), "azimuth_source": "demandé (squelette)",
                     "seed": best["seed"], "backend": backend, "prompt": text, "at": now()}
            if measure:
                entry["azimuth_measured"] = best["azimuth"]
                entry["azimuth_measure"] = {"engine": "sam3dbody", "yaw_raw": round(best["yaw"], 1),
                                            "reference": "front", "tries": [
                                                {"seed": t["seed"], "azimuth": t["azimuth"]} for t in tries],
                                            "at": now()}
            dest.with_suffix(".json").write_text(json.dumps(
                {"kind": "view", "method": method, "azimuth": az, "backend": backend, "seed": best["seed"],
                 "prompt": text, "refs": [body, p.rel(skel[az])],
                 "measured": entry.get("azimuth_measure")}, ensure_ascii=False, indent=2), encoding="utf-8")
            raw[n] = entry
    elif method == "orbit":
        azimuths = {n: prompts.AZIMUTHS[n][0] for n in names}
        sections = prompts.orbit(p.sheet, p.data["notes"], costume_prompt=cos["prompt"], style=p.data["style"])
        print(f"  orbite · {len(names)} azimuts à extraire · graine {s}")
        stills = h3.orbit(sections=sections, refs=refs, dest_dir=folder, seed=s, azimuths=azimuths,
                          report=report, identity_seed=identity_seed(p))
        for n, st in stills.items():
            raw[n] = {"file": p.rel(st.path), "azimuth": azimuths[n],
                      "azimuth_source": f"orbite, {st.meta.get('azimuth_source', 'supposé')}",
                      "azimuth_estimated": st.meta.get("azimuth_estimated"),
                      "precision_deg": st.meta.get("precision_deg"), "frame": st.meta.get("frame"),
                      "seed": s, "backend": st.backend, "at": now()}
    elif method in views_qwen.METHODS:
        source = p.path(body)
        if method == "qwen21-orbit":
            # Ce LoRA a appris sur des images détourées, fond transparent.
            rgba = imaging.matte(imaging.load(source), config.backend("prep"),
                                 workdir=p.dir(f"costumes/{key}/views/.matte"))
            source = folder / "_source_rgba.png"
            rgba.save(source)
        for n in names:
            az = prompts.AZIMUTHS[n][0]
            dest = folder / f"{n}.png"
            if n == "front":
                imaging.load(p.path(body)).save(dest)
                raw[n] = {"file": p.rel(dest), "azimuth": 0.0, "azimuth_source": "plein pied validé", "seed": None,
                          "backend": "reprise", "at": now()}
                continue
            print(f"  vue {n} · {az:g}° · {method} · graine {s}")
            meta = views_qwen.generate(method, n, source=source, dest=dest, seed=s, report=report)
            dest.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            raw[n] = {"file": p.rel(dest), "azimuth": az, "azimuth_source": f"demandé ({method})", "seed": s,
                      "backend": "comfyui", "prompt": meta["prompt"], "at": now()}
    else:
        for n in names:
            sections = prompts.view(p.sheet, p.data["notes"], name=n, costume_prompt=cos["prompt"],
                                    style=p.data["style"])
            az = prompts.AZIMUTHS[n][0]
            print(f"  vue {n} · {az:g}° · graine {s}")
            st = h3.still("view", sections=sections, refs=refs, dest=folder / f"{n}.png", seed=s, report=report,
                          extra={"identity_seed": identity_seed(p), "azimuth": az})
            raw[n] = {"file": p.rel(st.path), "azimuth": az, "azimuth_source": "demandé", "seed": s,
                      "backend": st.backend, "at": now()}
    contact = [(f"{n} · {e['azimuth']:g}°", p.path(e["file"])) for n, e in raw.items()]
    if bench:
        _contact(p, f"costumes/{key}/views/banc/{method}/contact.png", contact, cols=5)
        return raw
    v = cos["views"]
    v["method"] = method
    v["raw"].update(raw)
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
        images[n] = imaging.matte(img, engine, workdir=p.dir(f"costumes/{key}/views/.matte"))

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


def check(p: Project, costume: str | None, *, angles: dict[str, float] | None = None, measure: bool = False,
          report=None) -> dict:
    """Refus explicite au-delà de ±5° (§6.2). Les angles viennent, par
    ordre de préférence : de la ligne de commande, d'une mesure (SAM 3D
    Body, `measure`), d'une estimation (silhouette de l'orbite), ou de ce
    qui a été demandé au générateur — et le rapport dit lequel."""
    key, cos = p.costume(costume)
    v = cos["views"]
    if not v["prepared"]:
        raise ChainError("vues non préparées — `./usine prep` d'abord")
    if measure:
        from . import sam3d

        names = [n for n in (*ORTHO, "threequarter") if n in v["raw"]]
        got = sam3d.measure_azimuths({n: p.path(v["raw"][n]["file"]) for n in names},
                                     workdir=p.dir(f"costumes/{key}/views/.sam3d"), report=report or _report())
        for n, m in got.items():
            v["raw"][n]["azimuth_measured"] = m["azimuth"]
            v["raw"][n]["azimuth_measure"] = {"engine": "sam3dbody", "yaw_raw": m["yaw_raw"],
                                              "reference": "front", "at": now()}
    errors, table = {}, {}
    for n in ORTHO:
        target = prompts.AZIMUTHS[n][0]
        raw = v["raw"].get(n, {})
        if angles and n in angles:
            value, source = angles[n], "fourni"
        elif raw.get("azimuth_measured") is not None:
            value, source = raw["azimuth_measured"], "mesuré"
        elif raw.get("azimuth_estimated") is not None:
            value, source = raw["azimuth_estimated"], f"estimé : {raw.get('azimuth_source', '?')}"
        elif n in v["raw"]:
            value, source = raw["azimuth"], raw.get("azimuth_source", "demandé")
        else:
            errors[n] = "vue absente"
            continue
        delta = angular_error(value, target)
        table[n] = {"target": target, "value": value, "error": round(delta, 2), "source": source,
                    "precision_deg": raw.get("precision_deg")}
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

def height_m(sheet: dict, default: float = 1.75) -> float:
    """La taille du personnage, lue dans la fiche : « 172 cm », « 1,72 m »,
    « 5'8" » ou un nombre nu (en centimètres au-delà de 3)."""
    import re

    text = str(sheet.get("height") or "").lower().replace(",", ".")
    if m := re.search(r"(\d+)\s*'\s*(\d+)?", text):
        return round((int(m.group(1)) * 12 + int(m.group(2) or 0)) * 0.0254, 3)
    if m := re.search(r"\d+(\.\d+)?", text):
        v = float(m.group(0))
        return v / 100.0 if (v > 3.0 or "cm" in text) else v
    return default


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
                            single_view=single, texture=texture, height_m=height_m(p.sheet), report=report)
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
