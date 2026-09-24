"""La ligne de commande de la chaîne locale : `./usine <commande>`.

Une commande par étage, dans l'ordre du brief. Chaque commande dit ce
qu'elle a produit et où ; `./usine etat <perso>` dit où en est un
personnage et quelle est la prochaine commande à lancer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import chain, config
from .project import ChainError, Project, list_projects


def _p(args) -> Project:
    return Project.open(args.perso)


def _angles(pairs: list[str] | None) -> dict[str, float] | None:
    if not pairs:
        return None
    out = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if not value:
            raise ChainError(f"angle mal écrit : {pair} (attendu : left=91.5)")
        out[name.strip()] = float(value)
    return out


# ── commandes ──────────────────────────────────────────────────────

def cmd_nouveau(args) -> None:
    identity, notes = {}, []
    if args.identite:
        payload = json.loads(Path(args.identite).read_text(encoding="utf-8"))
        identity = payload.get("sheet", payload)
        notes = payload.get("notes", [])
    name = args.nom or identity.get("character_name")
    if not name:
        raise ChainError("donne un nom, ou une identité exportée qui en porte un")
    p = Project.create(name, style=args.style, identity=identity, notes=notes)
    filled = sum(1 for v in identity.values() if v)
    print(f"personnage créé : {p.root}")
    print(f"  fiche : {filled}/21 champs{' — remplis-la dans la page, étage Identité, puis ./usine identite' if filled < 21 else ''}")
    print(f"  suite : ./usine visage {p.data['slug']}")


def cmd_liste(args) -> None:
    projects = list_projects()
    if not projects:
        print(f"aucun personnage sous {config.projects_root()} — ./usine nouveau <nom>")
    for p in projects:
        print(f"{p.data['slug']:24s} {p.data['name']}  ·  {len(p.data['costumes'])} costume(s)")


def cmd_identite(args) -> None:
    p = _p(args)
    p.set_identity(json.loads(Path(args.fichier).read_text(encoding="utf-8")))
    p.save()
    print(f"fiche d'identité remplacée : {sum(1 for v in p.sheet.values() if v)}/21 champs")


def cmd_visage(args) -> None:
    p = _p(args)
    made = chain.face(p, prompt=args.prompt or "", refs=args.ref or [], variants=args.variantes, seed=args.graine)
    print(f"{len(made)} variante(s) ; planche contact : {p.path('face/contact.png')}")
    print(f"  choisir : ./usine visage-ok {p.data['slug']} <numéro>")


def cmd_visage_ok(args) -> None:
    p = _p(args)
    print(f"visage verrouillé : {p.path(chain.face_lock(p, args.candidat))}")
    print(f"  suite : ./usine costume {p.data['slug']} <nom> --ref vetement.png")


def cmd_costume(args) -> None:
    p = _p(args)
    key = chain.costume_add(p, args.nom, prompt=args.prompt or "", refs=args.ref or [])
    print(f"costume « {key} » créé, {len(args.ref or [])} référence(s)")
    print(f"  suite : ./usine pleinpied {p.data['slug']} --costume {key}")


def cmd_pleinpied(args) -> None:
    p = _p(args)
    made = chain.fullbody(p, args.costume, variants=args.variantes, seed=args.graine, prompt=args.prompt or "")
    key, _ = p.costume(args.costume)
    print(f"{len(made)} plein(s) pied(s) ; contact : {p.path(f'costumes/{key}/fullbody/contact.png')}")
    print(f"  choisir : ./usine pleinpied-ok {p.data['slug']} <numéro> --costume {key}")


def cmd_pleinpied_ok(args) -> None:
    p = _p(args)
    print(f"plein pied validé : {p.path(chain.fullbody_ok(p, args.costume, args.candidat))}")
    print(f"  suite : ./usine planche {p.data['slug']} --ab")


def cmd_planche(args) -> None:
    p = _p(args)
    made = chain.sheet(p, args.costume, mask_face=args.disque, ab=args.ab, seed=args.graine)
    for s in made:
        print(f"planche {s['id']}{' (disque)' if s['mask_face'] else ''} : {p.path(s['file'])}")
    print(f"  valider : ./usine planche-ok {p.data['slug']} <id>")


def cmd_planche_ok(args) -> None:
    p = _p(args)
    s = chain.sheet_ok(p, args.costume, args.id)
    print(f"planche {s['id']} validée{' (avec disque)' if s['mask_face'] else ''}")
    print(f"  suite : ./usine vues {p.data['slug']}")


def cmd_vues(args) -> None:
    p = _p(args)
    raw = chain.views(p, args.costume, method="orbit" if args.orbite else "per_view", names=args.vue,
                      threequarter=not args.sans_34, seed=args.graine)
    key, _ = p.costume(args.costume)
    print(f"{len(raw)} vue(s) ; contact : {p.path(f'costumes/{key}/views/contact_raw.png')}")
    print(f"  suite : ./usine prep {p.data['slug']}")


def cmd_prep(args) -> None:
    p = _p(args)
    rep = chain.prep(p, args.costume, delight=args.delight, size=args.taille, margin=args.marge)
    s = rep["summary"]
    print(f"vues préparées à {s['size']} px, marge {s['margin']:.0%} ; "
          f"écart de hauteur avant correction : {s['height_spread_before']:.1%}")
    print(f"  suite : ./usine controle {p.data['slug']}")


def cmd_controle(args) -> None:
    p = _p(args)
    res = chain.check(p, args.costume, angles=_angles(args.angle), measure=args.mesurer)
    for n, row in res["angles"].items():
        flag = "ok " if abs(row["error"]) <= res["tolerance"] else "NON"
        prec = f" ±{row['precision_deg']:g}°" if row.get("precision_deg") is not None else ""
        print(f"  {flag} {n:6s} cible {row['target']:5.1f}°  relevé {row['value']:6.1f}°{prec}  "
              f"écart {row['error']:+5.1f}°  ({row['source']})")
    if not res["measured"]:
        print("  note : aucun estimateur de pose n'a regardé les images — angles déclarés ou tirés de la silhouette")
    if res["ok"]:
        print(f"contrôle passé (±{res['tolerance']:g}°) ; suite : ./usine mesh {p.data['slug']}")
    else:
        raise ChainError("contrôle en échec : " + "; ".join(f"{n} {e}" for n, e in res["errors"].items()))


def cmd_mesh(args) -> None:
    p = _p(args)
    e = chain.mesh(p, args.costume, engine=args.moteur3d, single_view=args.une_vue, seed=args.graine,
                   texture=not args.sans_texture)
    st = e["stats"]
    print(f"mesh v{e['version']} ({e['engine']}, {e['backend']}) : {p.path(e['glb'])}")
    print(f"  {st['vertices']} sommets, {st['triangles']} triangles, taille {st['size_m']} m ; "
          f"canaux : {', '.join(e['maps']) or 'aucun'}")
    print(f"  voir : ./usine voir {p.data['slug']}")


def cmd_etat(args) -> None:
    from .status import render

    print(render(_p(args)))


def cmd_voir(args) -> None:
    from .viewer import serve

    serve(_p(args), costume=args.costume, a=args.a, b=args.b, port=args.port, open_browser=not args.sans_navigateur)


def cmd_page(args) -> None:
    from .viewer import serve_page

    serve_page(port=args.port, open_browser=not args.sans_navigateur)


def cmd_gabarit(args) -> None:
    from .gabarit import run

    run(args.fichier, name=args.nom, force=args.force)


def cmd_doctor(args) -> None:
    from .doctor import run

    run(write=args.ecrire)


# ── analyse des arguments ──────────────────────────────────────────

def build() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="usine", description="Character Factory — la chaîne, en local.")
    ap.add_argument("--moteur", action="append", metavar="CAP=MOTEUR",
                    help="force un moteur pour cet appel, ex. --moteur h3=stub (répétable)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="commande")

    def cmd(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    def perso(sp):
        sp.add_argument("perso", help="nom ou dossier du personnage")

    def costume(sp):
        sp.add_argument("--costume", help="nom du costume (facultatif s'il n'y en a qu'un)")

    sp = cmd("nouveau", cmd_nouveau, "créer un personnage")
    sp.add_argument("nom", nargs="?")
    sp.add_argument("--identite", help="identité exportée par la page (étage Identité)")
    sp.add_argument("--style", choices=["photoreal", "stylized"], default="photoreal")

    cmd("liste", cmd_liste, "lister les personnages")

    sp = cmd("etat", cmd_etat, "où en est un personnage")
    perso(sp)

    sp = cmd("identite", cmd_identite, "remplacer la fiche par un export de la page")
    perso(sp)
    sp.add_argument("fichier")

    sp = cmd("visage", cmd_visage, "variantes du portrait neutre (§3)")
    perso(sp)
    sp.add_argument("--prompt")
    sp.add_argument("--ref", action="append", help="photo source (normalisation)")
    sp.add_argument("--variantes", type=int, default=4)
    sp.add_argument("--graine", type=int)

    sp = cmd("visage-ok", cmd_visage_ok, "verrouiller un visage — une seule fois")
    perso(sp)
    sp.add_argument("candidat", help="numéro du candidat, ou nom du fichier")

    sp = cmd("costume", cmd_costume, "créer un costume (§4)")
    perso(sp)
    sp.add_argument("nom")
    sp.add_argument("--prompt")
    sp.add_argument("--ref", action="append", help="image de vêtement (répétable)")

    sp = cmd("pleinpied", cmd_pleinpied, "plein pied habillé, visage verrouillé en référence")
    perso(sp), costume(sp)
    sp.add_argument("--prompt")
    sp.add_argument("--variantes", type=int, default=2)
    sp.add_argument("--graine", type=int)

    sp = cmd("pleinpied-ok", cmd_pleinpied_ok, "valider un plein pied")
    perso(sp), costume(sp)
    sp.add_argument("candidat")

    sp = cmd("planche", cmd_planche, "character sheet H3 en cinq frames (§5)")
    perso(sp), costume(sp)
    sp.add_argument("--disque", action="store_true", help="disque neutre sur le visage des plein pieds")
    sp.add_argument("--ab", action="store_true", help="deux planches, avec et sans disque, même graine")
    sp.add_argument("--graine", type=int)

    sp = cmd("planche-ok", cmd_planche_ok, "valider une planche")
    perso(sp), costume(sp)
    sp.add_argument("id", help="identifiant de la planche, ex. s002")

    sp = cmd("vues", cmd_vues, "vues orthogonales plein cadre (§6)")
    perso(sp), costume(sp)
    sp.add_argument("--orbite", action="store_true", help="un plan en orbite redécoupé, au lieu d'une génération par vue")
    sp.add_argument("--sans-34", action="store_true", help="ne pas générer la vue 3/4")
    sp.add_argument("--vue", action="append", choices=["front", "left", "back", "right", "threequarter"],
                    help="ne refaire que cette vue (répétable) ; les autres restent")
    sp.add_argument("--graine", type=int)

    sp = cmd("prep", cmd_prep, "détourage, delight, recentrage, marges égales (§6.2, §6.3)")
    perso(sp), costume(sp)
    sp.add_argument("--delight", action=argparse.BooleanOptionalAction, default=None)
    sp.add_argument("--taille", type=int, default=1024)
    sp.add_argument("--marge", type=float, default=0.08)

    sp = cmd("controle", cmd_controle, "contrôle d'alignement, ±5° (§6.2)")
    perso(sp), costume(sp)
    sp.add_argument("--angle", action="append", metavar="VUE=DEGRÉS", help="angle relevé à la main, ex. left=93")
    sp.add_argument("--mesurer", action="store_true",
                    help="mesurer l'azimut de chaque vue par SAM 3D Body (ComfyUI), relatif à la face")

    sp = cmd("mesh", cmd_mesh, "mesh PBR — TRELLIS 2 par défaut, ou Hunyuan3D 2.1 (§7)")
    perso(sp), costume(sp)
    sp.add_argument("--moteur3d", choices=["trellis2", "hunyuan3d-2.1"], default=None)
    sp.add_argument("--une-vue", action="store_true", help="image unique (le 3/4)")
    sp.add_argument("--sans-texture", action="store_true")
    sp.add_argument("--graine", type=int)

    sp = cmd("voir", cmd_voir, "ouvrir le viewer 3D sur les meshes d'un costume")
    perso(sp), costume(sp)
    sp.add_argument("--a", help="version A (défaut : la dernière), ex. 2 ou rig:1")
    sp.add_argument("--b", help="version B, pour comparer")
    sp.add_argument("--port", type=int, default=8766)
    sp.add_argument("--sans-navigateur", action="store_true")

    sp = cmd("page", cmd_page, "servir la page en local (étage Identité, conversation avec le modèle)")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--sans-navigateur", action="store_true")

    sp = cmd("gabarit", cmd_gabarit, "adopter un workflow ComfyUI exporté au format API comme gabarit H3")
    sp.add_argument("fichier", help="le workflow exporté (Workflow → Export (API))")
    sp.add_argument("--nom", default="h3_ref2va.json", help="h3_ref2va.json (images fixes) ou h3_orbit.json")
    sp.add_argument("--force", action="store_true", help="remplacer un gabarit existant")

    sp = cmd("doctor", cmd_doctor, "ce qui tourne sur la machine, et quels moteurs régler")
    sp.add_argument("--ecrire", action="store_true", help="écrire factory.local.json avec ce qui a été trouvé")

    from .cli_motion import register

    register(sub, cmd, perso, costume)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build().parse_args(argv)
    try:
        for pair in args.moteur or []:
            cap, _, value = pair.partition("=")
            config.force_backend(cap.strip(), value.strip())
        args.fn(args)
        return 0
    except ChainError as exc:
        print(f"refusé : {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrompu", file=sys.stderr)
        return 130
