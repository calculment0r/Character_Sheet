"""Où en est un personnage : les huit étages, et la commande suivante."""

from __future__ import annotations

from .project import Project


def _fake(entry: dict | None) -> str:
    return " (factice)" if entry and entry.get("backend") == "stub" else ""


def render(p: Project) -> str:
    d = p.data
    slug = d["slug"]
    lines = [f"{d['name']} ({slug}) · {d['style']} · {p.root}"]
    nxt: str | None = None

    def row(ref: str, name: str, text: str) -> None:
        lines.append(f"  {ref}  {name:18s} {text}")

    filled = sum(1 for v in p.sheet.values() if v)
    row("ST-01", "Identité", f"{filled}/21 champs, {len(d['notes'])} note(s)")

    face = d["face"]
    if face.get("locked"):
        locked = next((c for c in face["candidates"] if c["file"] == face.get("locked_from")), None)
        row("ST-02", "Visage", f"verrouillé — {face.get('locked_from')} · graine {face.get('locked_seed')}{_fake(locked)}")
    else:
        row("ST-02", "Visage", f"{len(face['candidates'])} candidat(s), aucun verrouillé")
        nxt = nxt or (f"./usine visage-ok {slug} <numéro>" if face["candidates"] else f"./usine visage {slug}")

    costumes = d["costumes"]
    row("ST-03", "Costumes", f"{len(costumes)} costume(s)" + (f" : {', '.join(costumes)}" if costumes else ""))
    if not costumes and face.get("locked"):
        nxt = nxt or f"./usine costume {slug} <nom> --ref vetement.png"

    for key, cos in costumes.items():
        lines.append(f"  ── costume {key} ──")
        fb = cos["fullbody"]
        if fb.get("validated"):
            row("", "  plein pied", "validé")
        else:
            row("", "  plein pied", f"{len(fb['candidates'])} candidat(s), aucun validé")
            nxt = nxt or (f"./usine pleinpied-ok {slug} <numéro> --costume {key}" if fb["candidates"]
                          else f"./usine pleinpied {slug} --costume {key}")
            continue

        sheet = next((s for s in cos["sheets"] if s["id"] == cos.get("sheet")), None)
        if sheet:
            row("ST-04", "Planche", f"{sheet['id']} validée{' (disque)' if sheet['mask_face'] else ''}"
                                    f"{_fake(sheet)} · {len(cos['sheets'])} planche(s)")
        else:
            row("ST-04", "Planche", f"{len(cos['sheets'])} planche(s), aucune validée")
            nxt = nxt or (f"./usine planche-ok {slug} <id> --costume {key}" if cos["sheets"]
                          else f"./usine planche {slug} --costume {key} --ab")
            continue

        v = cos["views"]
        chk = v.get("check")
        state = f"{len(v['raw'])} brute(s)"
        if v["prepared"]:
            state += f", préparées{' + delight' if v.get('delighted') else ''}"
        if chk:
            state += (", contrôle ok" if chk["ok"] else ", contrôle EN ÉCHEC") + \
                     ("" if chk.get("measured") else " (angles déclarés)")
        row("ST-05", "Vues", state + _fake(next(iter(v["raw"].values()), None)))
        if not v["raw"]:
            nxt = nxt or f"./usine vues {slug} --costume {key}"
            continue
        if not v["prepared"]:
            nxt = nxt or f"./usine prep {slug} --costume {key}"
            continue
        if not chk:
            nxt = nxt or f"./usine controle {slug} --costume {key}"
            continue

        if cos["meshes"]:
            m = cos["meshes"][-1]
            row("ST-06", "Mesh 3D", f"v{m['version']} {m['engine']}{_fake(m)} · {m['stats']['vertices']} sommets"
                                   f" · {len(cos['meshes'])} version(s)")
        else:
            row("ST-06", "Mesh 3D", "aucun")
            nxt = nxt or f"./usine mesh {slug} --costume {key}"
            continue

        if cos["rigs"]:
            r = cos["rigs"][-1]
            verdict = {"unseen": "pas encore regardé", "accepted": "accepté", "rejected": "refusé"}[r["verdict"]]
            row("ST-07", "Rig", f"v{r['version']} sur mesh v{r['mesh']}{_fake(r)} · {verdict}")
            if r["verdict"] == "unseen":
                nxt = nxt or f"./usine voir {slug} --costume {key} --a rig:{r['version']}"
        else:
            row("ST-07", "Rig", "aucun")
            nxt = nxt or f"./usine rig {slug} --costume {key}"

    takes = d["takes"]
    baked = [n for n, t in d["timelines"].items() if t.get("baked")]
    row("ST-08", "Animation", f"{len(takes)} prise(s), {len(d['timelines'])} timeline(s), {len(baked)} cuite(s)")
    if nxt:
        lines.append(f"  suite : {nxt}")
    return "\n".join(lines)
