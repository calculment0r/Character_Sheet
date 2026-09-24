"""Les commandes de l'aval : rig, prises, timeline, cuisson."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import bake as bake_mod
from . import motion, rig as rig_mod
from .project import ChainError, Project, now


def _p(args) -> Project:
    return Project.open(args.perso)


# ── rig ────────────────────────────────────────────────────────────

def cmd_rig(args) -> None:
    if args.pose == "tpose":
        raise ChainError("le bind se fait en A-pose ; la conversion vers la T-pose SOMA est un delta, pas un "
                         "bind (§8.2). On ne modélise jamais en T-pose.")
    p = _p(args)
    key, cos = p.costume(args.costume)
    if not cos["meshes"]:
        raise ChainError("aucun mesh pour ce costume — `./usine mesh` d'abord")
    mesh = cos["meshes"][-1] if args.mesh is None else next(
        (m for m in cos["meshes"] if m["version"] == args.mesh), None)
    if mesh is None:
        raise ChainError(f"mesh v{args.mesh} introuvable")
    version = len(cos["rigs"]) + 1
    out_dir = p.dir(f"costumes/{key}/rig/v{version:03d}")

    def report(pr, msg):
        print(f"    {int(pr * 100):3d} %  {msg}", flush=True)

    try:
        res = rig_mod.build(mesh_glb=p.path(mesh["glb"]), out_dir=out_dir, report=report)
    except rig_mod.RigRefused as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise ChainError(str(exc)) from exc
    entry = {"version": version, "mesh": mesh["version"], "backend": res["backend"], "dir": p.rel(out_dir),
             "glb": p.rel(res["glb"]), "skeleton": p.rel(res["skeleton"]),
             "bind_delta": p.rel(res["bind_delta"]), "meta": res["meta"], "verdict": "unseen", "at": now()}
    cos["rigs"].append(entry)
    p.save()
    print(f"rig v{version} sur mesh v{mesh['version']} ({res['backend']}) : {p.path(entry['glb'])}")
    print(f"  bras à {res['meta']['arm_angle_deg']}° de la verticale (A-pose) · "
          f"{res['meta']['joints']} articulations · hanches à {res['meta']['hip_height_m']} m")
    print(f"  regarder les cinq poses : ./usine voir {p.data['slug']} --costume {key} --a rig:{version}")
    print(f"  puis : ./usine rig-ok {p.data['slug']} accepte --costume {key}")


def cmd_rig_ok(args) -> None:
    p = _p(args)
    key, cos = p.costume(args.costume)
    if not cos["rigs"]:
        raise ChainError("aucun rig pour ce costume")
    rig = cos["rigs"][-1] if args.rig is None else next((r for r in cos["rigs"] if r["version"] == args.rig), None)
    if rig is None:
        raise ChainError(f"rig v{args.rig} introuvable")
    rig["verdict"] = "accepted" if args.verdict == "accepte" else "rejected"
    rig["verdict_at"] = now()
    p.save()
    print(f"rig v{rig['version']} : {'accepté' if rig['verdict'] == 'accepted' else 'refusé'}")


# ── prises ─────────────────────────────────────────────────────────

def cmd_prise(args) -> None:
    p = _p(args)
    if sum(bool(x) for x in (args.kimodo, args.video, args.main)) != 1:
        raise ChainError("une prise vient d'un texte (--kimodo \"…\"), d'une vidéo (--video clip.mp4), "
                         "ou de la bibliothèque de mains (--main poing)")
    tid = p.next_id("t", p.data["takes"])
    folder = p.dir(f"takes/{tid}")
    dest = folder / "motion.npz"

    def report(pr, msg):
        print(f"    {int(pr * 100):3d} %  {msg}", flush=True)

    if args.kimodo:
        constraints = motion.load_constraints(args.contraintes)
        if constraints:
            (folder / "constraints.json").write_text(json.dumps(constraints, indent=2), encoding="utf-8")
        seed = args.graine if args.graine is not None else 0
        res = motion.kimodo(prompt=args.kimodo, frames=args.frames, fps=args.fps, constraints=constraints,
                            heading=args.cap, seed=seed, dest=dest, report=report)
        entry = {"id": tid, "name": args.nom or tid, "source": "authored", "engine": "kimodo",
                 "prompt": args.kimodo, "constraints": bool(constraints)}
    elif args.main:
        res = motion.hand_pose(pose=args.main, frames=args.frames, fps=args.fps, dest=dest)
        entry = {"id": tid, "name": args.nom or f"mains-{args.main}", "source": "authored", "engine": "mains",
                 "pose": args.main}
    else:
        video = p.import_file(args.video, f"takes/{tid}")
        res = motion.sam3dbody(video=p.path(video), fps=args.fps, dest=dest, report=report)
        entry = {"id": tid, "name": args.nom or tid, "source": "video", "engine": "sam3dbody", "media": video}
    info = motion.describe(dest)
    entry.update(npz=p.rel(dest), frames=info["frames"], fps=info["fps"], backend=res["backend"], at=now())
    p.data["takes"].append(entry)
    p.save()
    print(f"prise {tid} ({entry['engine']}, {res['backend']}) : {info['frames']} frames à {info['fps']:g} i/s")
    print(f"  suite : ./usine timeline {p.data['slug']} <nom> --piste body:{tid}")


# ── timeline ───────────────────────────────────────────────────────

KEYS = {"in": "in", "out": "out", "at": "at", "weight": "weight", "poids": "weight",
        "warp": "time_warp", "vitesse": "time_warp", "fade": "fade", "fondu": "fade"}


def _track(spec: str, takes: set[str]) -> dict:
    """`body:t001:at=30:fade=12` → une piste."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise ChainError(f"piste mal écrite : {spec} (attendu : type:prise[:clé=valeur…])")
    kind, tid = parts[0], parts[1]
    if kind not in ("body", "hands_l", "hands_r", "face"):
        raise ChainError(f"type de piste inconnu : {kind} (body, hands_l, hands_r, face)")
    if tid not in takes:
        raise ChainError(f"prise inconnue : {tid}")
    track = {"type": kind, "take_id": tid, "in": 0, "out": 0, "at": 0, "weight": 1.0, "time_warp": 1.0, "fade": 0}
    for kv in parts[2:]:
        k, _, v = kv.partition("=")
        if k not in KEYS or not v:
            raise ChainError(f"réglage de piste inconnu : {kv}")
        track[KEYS[k]] = float(v) if KEYS[k] in ("weight", "time_warp") else int(v)
    return track


def cmd_timeline(args) -> None:
    p = _p(args)
    takes = {t["id"] for t in p.data["takes"]}
    if args.fichier:
        tracks = json.loads(Path(args.fichier).read_text(encoding="utf-8"))
        for tr in tracks:
            if tr.get("take_id") not in takes:
                raise ChainError(f"prise inconnue : {tr.get('take_id')}")
    else:
        tracks = [_track(s, takes) for s in args.piste or []]
    if not tracks:
        raise ChainError("aucune piste — --piste body:t001, ou --fichier pistes.json")
    p.data["timelines"][args.nom] = {"fps": args.fps, "tracks": tracks, "baked": None, "at": now()}
    p.save()
    print(f"timeline « {args.nom} » : {len(tracks)} piste(s) à {args.fps:g} i/s")
    print(f"  cuire : ./usine bake {p.data['slug']} {args.nom}")


def cmd_bake(args) -> None:
    p = _p(args)
    tl = p.data["timelines"].get(args.nom)
    if tl is None:
        raise ChainError(f"timeline inconnue : {args.nom}")
    key, cos = p.costume(args.costume)
    rigs = [r for r in cos["rigs"] if r["verdict"] != "rejected"]
    if not rigs:
        raise ChainError("aucun rig utilisable pour ce costume — `./usine rig` d'abord")
    rig = rigs[-1] if args.rig is None else next((r for r in rigs if r["version"] == args.rig), None)
    if rig is None:
        raise ChainError(f"rig v{args.rig} introuvable ou refusé")
    if rig["verdict"] == "unseen":
        print("  note : ce rig n'a pas encore été accepté — regarde ses cinq poses de contrôle")
    by_id = {t["id"]: p.path(t["npz"]) for t in p.data["takes"]}
    out_dir = p.dir(f"bake/{args.nom}")
    res = bake_mod.bake(tracks=tl["tracks"], takes={t["take_id"]: by_id[t["take_id"]] for t in tl["tracks"]},
                        rig_dir=p.path(rig["dir"]), fps=tl["fps"], out_dir=out_dir, name=args.nom)
    tl["baked"] = {"npz": p.rel(res["npz"]), "glb": p.rel(res["glb"]), "costume": key, "rig": rig["version"],
                   "frames": res["meta"]["frames"], "at": now()}
    p.save()
    m = res["meta"]
    print(f"timeline « {args.nom} » cuite : {m['frames']} frames, {m['seconds']} s, racine ×{m['root_scale']}")
    print(f"  {p.path(tl['baked']['npz'])}")
    print(f"  {p.path(tl['baked']['glb'])}")
    print(f"  voir : ./usine voir {p.data['slug']} --costume {key} --a bake:{args.nom}")


def register(sub, cmd, perso, costume) -> None:
    sp = cmd("rig", cmd_rig, "auto-rig SOMA 77, bind en A-pose, cinq poses de contrôle (§10)")
    perso(sp), costume(sp)
    sp.add_argument("--mesh", type=int, help="version du mesh (défaut : la dernière)")
    sp.add_argument("--pose", choices=["apose", "tpose"], default="apose")

    sp = cmd("rig-ok", cmd_rig_ok, "accepter ou refuser un rig après ses poses de contrôle")
    perso(sp), costume(sp)
    sp.add_argument("verdict", choices=["accepte", "refuse"])
    sp.add_argument("--rig", type=int)

    sp = cmd("prise", cmd_prise, "une prise de mouvement : Kimodo (texte) ou SAM 3D Body (vidéo) (§11, §12)")
    perso(sp)
    sp.add_argument("--kimodo", metavar="TEXTE", help="le prompt de mouvement")
    sp.add_argument("--video", help="la vidéo source")
    sp.add_argument("--main", choices=["detendue", "ouverte", "poing", "pince", "pointer"],
                    help="une pose de main figée, à poser sur une piste hands_l ou hands_r (§13.1)")
    sp.add_argument("--contraintes", help="JSON de contraintes Kimodo (§12.2)")
    sp.add_argument("--frames", type=int, default=120)
    sp.add_argument("--fps", type=float, default=30.0)
    sp.add_argument("--cap", type=float, default=0.0, help="cap initial en radians, 0 = face à +Z")
    sp.add_argument("--nom")
    sp.add_argument("--graine", type=int)

    sp = cmd("timeline", cmd_timeline, "composer une timeline de pistes (§11.3)")
    perso(sp)
    sp.add_argument("nom")
    sp.add_argument("--piste", action="append", metavar="TYPE:PRISE[:clé=valeur]",
                    help="ex. body:t001  hands_r:t002:at=30:fade=10:poids=0.8  (clés : in out at poids vitesse fondu)")
    sp.add_argument("--fichier", help="les pistes en JSON")
    sp.add_argument("--fps", type=float, default=30.0)

    sp = cmd("bake", cmd_bake, "cuire une timeline : NPZ somaskel77 + GLB animé")
    perso(sp), costume(sp)
    sp.add_argument("nom")
    sp.add_argument("--rig", type=int)
