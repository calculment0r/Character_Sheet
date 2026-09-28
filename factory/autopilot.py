"""L'autopilote : les étages techniques, menés et validés par la machine.

Décision de Cal (27/09) : il ne choisit que ce qui est affaire de goût —
visage, tenue et plein pied, voix, expressions. L'A-pose, les vues, la
préparation, le contrôle, le mesh et le rig tournent seuls, derrière,
dès qu'un plein pied est validé ; chacun est validé par une mesure, et
la validation le dit (`validated_by: "auto"`, avec la mesure). Cal n'est
appelé que quand un étage échoue deux fois : une entrée « ce qui
attend » (`data["attention"]`), avec ce qu'il peut faire.

Les étapes, une par travail de la file du studio, pour qu'un travail de
Cal passe devant entre deux (`studio.py`) :

  apose:gen      les A-poses du tour (Qwen-Image 2.1, squelette DWPose),
                 quatre par tour, tirées d'un coup sur les deux machines ;
  apose:pick     chaque proposition relevée par DWPose et notée contre le
                 plein pied (`apose_pick.py`) ; la meilleure recevable est
                 validée ; sinon un tour de plus, trois tours au plus, puis
                 Cal choisit parmi les propositions ;
  views:<noms>   les vues par groupes (face et gauche, dos, droite, 3/4) :
                 chacune se mesure et se relance déjà seule (SAM 3D Body,
                 `chain.views`) ;
  prep, check    détourage et contrôle ±5° ; une vue hors tolérance est
                 refaite, deux fois au plus, puis Cal est appelé ;
  mesh           TRELLIS.2 multi-vues ; mesh vide ou aberrant refusé ;
  rig            UniRig → SOMA ; le rig est mesuré en rejouant ses poses de
                 contrôle (`rigcheck.py`) et accepté s'il passe.

Une exception sur une étape la relance une fois ; la seconde appelle Cal.
L'état vit dans le manifeste (`cos["autopilot"]`) : un studio relancé
reprend où il en était. Ce qui est fait se lit dans le manifeste, pas
dans l'état : l'étape suivante se déduit toujours de ce qui existe.
"""

from __future__ import annotations

import os
import uuid

from . import chain, config, h3
from .project import ChainError, Project, apose as apose_of, fullbody_token, now

APOSE_BATCH = 4       # propositions par tour : deux par machine (DGX1, DGX2)
APOSE_ROUNDS = 3      # tours avant d'appeler Cal
VIEW_ROUNDS = 2       # reprises des vues hors tolérance
STEP_TRIES = 2        # une exception deux fois sur la même étape : Cal
# deux vues à rendre par étape : une par machine (DGX1, DGX2), et des
# étapes courtes que le travail de Cal peut toujours doubler
VIEW_GROUPS = (("front", "left", "right"), ("back", "threequarter"))
LOG_LINES = 60

KIND = {"apose": "apose_failed", "views": "views_failed", "prep": "views_failed", "check": "views_failed",
        "mesh": "mesh_failed", "rig": "rig_failed"}
TITLE = {"apose_failed": "A-pose introuvable", "views_failed": "vues hors tolérance",
         "mesh_failed": "mesh en échec", "rig_failed": "rig en échec", "rig_review": "rig accepté, à regarder",
         "autopilot_failed": "autopilote arrêté"}

# Où calcule chaque étape (famille de modèles, capacité), pour la mémoire.
SPOT = {"apose:gen": ("qwen21", "portrait"), "views": ("qwen21", "portrait"), "prep": ("birefnet", "prep"),
        "mesh": ("trellis", "trellis"), "rig": ("unirig", None)}


def enabled() -> bool:
    return config.setting("autopilot", "on").lower() not in ("off", "0", "false", "non")


def state(cos: dict) -> dict:
    return cos.setdefault("autopilot", {"state": "off"})


def _stub_fail(stage: str, capability: str) -> bool:
    """FACTORY_STUB_FAIL=apose,mesh… : en factice seulement, fait échouer
    une étape, pour essayer les refus et l'appel à Cal."""
    return config.backend(capability) == "stub" and stage in os.getenv("FACTORY_STUB_FAIL", "").split(",")


def _log(st: dict, message: str) -> None:
    st.setdefault("log", []).append({"at": now(), "step": st.get("step"), "message": message})
    del st["log"][:-LOG_LINES]
    print(f"  autopilote · {message}")


# ── ce qui attend Cal ──────────────────────────────────────────────

def attention(p: Project) -> list[dict]:
    return p.data.setdefault("attention", [])


def open_items(p: Project) -> list[dict]:
    return [a for a in attention(p) if not a.get("resolved")]


def raise_attention(p: Project, kind: str, text: str, *, costume: str | None = None,
                    options: list[dict] | None = None, detail: dict | None = None) -> dict:
    """Une entrée « ce qui attend » ; une seule ouverte par costume et par
    sorte : la nouvelle remplace l'ancienne."""
    for a in open_items(p):
        if a["kind"] == kind and a.get("costume") == costume:
            a["resolved"] = {"at": now(), "by": "auto", "action": "remplacée"}
    item = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": TITLE.get(kind, kind), "text": text,
            "costume": costume, "options": options or [{"action": "retry", "label": "relancer"},
                                                        {"action": "dismiss", "label": "ignorer"}],
            "at": now(), "resolved": None}
    if detail:
        item["detail"] = detail
    attention(p).append(item)
    return item


def settle(p: Project, costume: str, kinds: tuple[str, ...], how: str = "passé") -> None:
    """Clôt les entrées d'un costume qu'une étape réussie rend caduques."""
    for a in open_items(p):
        if a.get("costume") == costume and a["kind"] in kinds:
            a["resolved"] = {"at": now(), "by": "auto", "action": how}


def resolve(p: Project, item_id: str, action: str, *, candidate: str | None = None) -> dict:
    """La réponse de Cal à une entrée : `dismiss` la range, `retry` relance
    l'autopilote là où il a échoué, `choose` valide l'A-pose qu'il désigne
    et relance l'autopilote pour la suite. Rend {"autopilot": costume} si
    un travail doit repartir."""
    item = next((a for a in attention(p) if a["id"] == item_id), None)
    if item is None:
        raise ChainError(f"entrée inconnue : {item_id}")
    if item.get("resolved"):
        raise ChainError("cette entrée est déjà réglée")
    key = item.get("costume")
    out: dict = {"id": item_id}
    if action == "choose":
        if item["kind"] != "apose_failed" or not candidate:
            raise ChainError("« choisir » demande une A-pose : candidate=<n°>")
        p.validate_apose(key, str(candidate), by="cal")
        out["autopilot"] = resume(p, key, why=f"A-pose n° {candidate} choisie par Cal")
    elif action == "retry":
        if key is None:
            raise ChainError("cette entrée ne porte sur aucun costume")
        out["autopilot"] = resume(p, key, why="relancé par Cal", fresh=True)
    elif action != "dismiss":
        raise ChainError(f"réponse inconnue : {action} (dismiss, retry, choose)")
    item["resolved"] = {"at": now(), "by": "cal", "action": action,
                        **({"candidate": str(candidate)} if candidate else {})}
    p.save()
    return out


# ── marche, arrêt ──────────────────────────────────────────────────

def start(p: Project, key: str, *, why: str) -> str:
    """Un nouveau départ, depuis le plein pied validé : l'aval a déjà été
    rendu caduc par la validation (`Project.validate_fullbody`)."""
    _, cos = p.costume(key)
    p.require_fullbody(cos)
    old = state(cos)
    cos["autopilot"] = {"state": "running", "step": None, "since": now(), "at": now(),
                        "fullbody": fullbody_token(cos), "apose_round": 0, "apose_base": 0,
                        "views_round": 0, "views_redo": [], "tries": {}, "failed": None,
                        "log": (old.get("log") or [])[-10:]}
    settle(p, key, tuple(KIND.values()) + ("rig_review",), "nouveau plein pied")
    _log(cos["autopilot"], f"départ : {why}")
    p.save()
    return key


def resume(p: Project, key: str, *, why: str, fresh: bool = False) -> str:
    """Reprend un autopilote arrêté ou en échec. `fresh` rend à l'étape
    fautive son budget d'essais et de tours."""
    _, cos = p.costume(key)
    st = state(cos)
    if st.get("state") in (None, "off") or st.get("fullbody") != fullbody_token(cos):
        return start(p, key, why=why)
    if fresh:
        failed = (st.get("failed") or {}).get("step") or st.get("step") or ""
        st["tries"] = {}
        if failed.startswith("apose"):
            st["apose_base"] = len(_round_candidates(cos, st))
            st["apose_round"] = 0
        if failed.split(":")[0] in ("views", "prep", "check"):
            st["views_round"] = 0
            chk = cos["views"].get("check") or {}
            st["views_redo"] = sorted(chk.get("errors") or {})
    st.update(state="running", failed=None, at=now())
    _log(st, f"reprise : {why}")
    p.save()
    return key


def stop(p: Project, key: str, *, why: str = "arrêté par Cal") -> None:
    _, cos = p.costume(key)
    st = state(cos)
    if st.get("state") == "running":
        st.update(state="paused", at=now())
        _log(st, why)
        p.save()


# ── l'étape suivante ───────────────────────────────────────────────

def _round_candidates(cos: dict, st: dict) -> list[tuple[int, dict]]:
    """Les A-poses tirées du plein pied en cours, avec leur n° (1…)."""
    return [(i + 1, c) for i, c in enumerate(apose_of(cos)["candidates"]) if c.get("fullbody") == st.get("fullbody")]


def _current_mesh(cos: dict) -> dict | None:
    """Le mesh fait des vues contrôlées en cours."""
    chk = cos["views"].get("check") or {}
    if not chk.get("ok"):
        return None
    for m in reversed(cos["meshes"]):
        if m.get("single_view") or m.get("rejected"):
            continue
        if m.get("views_check") == chk["at"] or (m.get("views_check") is None and m["at"] >= chk["at"]):
            return m
    return None


def plan(p: Project, key: str) -> str | None:
    """L'étape à jouer maintenant, lue dans le manifeste ; None : fini."""
    _, cos = p.costume(key)
    st = state(cos)
    if not cos["fullbody"].get("validated"):
        return None
    if not apose_of(cos).get("validated"):
        need = st.get("apose_base", 0) + APOSE_BATCH * (st.get("apose_round", 0) + 1)
        return "apose:gen" if len(_round_candidates(cos, st)) < need else "apose:pick"
    v = cos["views"]
    redo = set(st.get("views_redo") or [])
    for group in VIEW_GROUPS:
        names = [n for n in group if n not in v["raw"] or n in redo]
        if names:
            return "views:" + ",".join(names)
    if not v["prepared"]:
        return "prep"
    if not v.get("check"):
        return "check"
    if not v["check"].get("ok"):
        return "check"      # le contrôle en échec décide : reprise ou appel
    mesh = _current_mesh(cos)
    if mesh is None:
        return "mesh"
    if not any(r["mesh"] == mesh["version"] and r["verdict"] == "accepted" for r in cos["rigs"]):
        return "rig"
    return None


def spot(p: Project, key: str):
    step = plan(p, key)
    if step is None:
        return None
    return SPOT.get(step) or SPOT.get(step.split(":")[0])


# ── une étape ──────────────────────────────────────────────────────

class Hopeless(ChainError):
    """Un échec qu'une relance ne changera pas (moteur factice dans une
    vraie chaîne, par exemple) : Cal tout de suite."""


def run_step(p: Project, key: str, report, cancelled=lambda: False) -> dict:
    """Joue l'étape suivante. Rend {"step", "continue"} : `continue` dit
    s'il faut remettre l'autopilote en file."""
    _, cos = p.costume(key)
    st = state(cos)
    if st.get("state") != "running":
        return {"step": None, "continue": False, "state": st.get("state")}
    if st.get("fullbody") != fullbody_token(cos):
        # Le plein pied a été revalidé ailleurs (ligne de commande) : on repart de lui.
        start(p, key, why="plein pied revalidé")
        return {"step": None, "continue": True, "state": "running"}
    step = plan(p, key)
    if step is None:
        st.update(state="done", step=None, at=now())
        _log(st, "fini : tout est validé par la mesure")
        p.save()
        return {"step": None, "continue": False, "state": "done"}
    st.update(step=step, at=now())
    p.save()
    kind, _, arg = step.partition(":")
    try:
        STEPS[kind](p, key, cos, st, arg, report)
        st.get("tries", {}).pop(step, None)
    except Exception as exc:  # noqa: BLE001 — chaque échec se compte et se range
        if cancelled():
            st.update(state="paused", at=now())
            _log(st, f"{step} : annulé, autopilote en pause")
        else:
            tries = st.setdefault("tries", {})
            tries[step] = tries.get(step, 0) + 1
            _log(st, f"{step} : échec {tries[step]}/{STEP_TRIES} — {exc}")
            if tries[step] >= STEP_TRIES or isinstance(exc, Hopeless):
                why = f"{step} : {exc}" if isinstance(exc, Hopeless) else f"{step} a échoué deux fois : {exc}"
                _escalate(p, key, st, KIND[kind], why, step=step)
    live = state(cos)       # un nouveau départ a pu remplacer l'état pendant l'étape
    live["at"] = now()
    p.save()
    return {"step": step, "continue": live.get("state") == "running", "state": live.get("state")}


def crashed(p: Project, key: str, message: str) -> bool:
    """Un travail d'autopilote mort hors d'une étape (mémoire trop basse,
    manifeste illisible…) : compté comme un échec de l'étape en cours.
    Rend vrai s'il faut réessayer."""
    _, cos = p.costume(key)
    st = state(cos)
    if st.get("state") != "running":
        return False
    step = st.get("step") or "?"
    tries = st.setdefault("tries", {})
    tries[step] = tries.get(step, 0) + 1
    _log(st, f"{step} : travail en échec {tries[step]}/{STEP_TRIES} — {message}")
    if tries[step] >= STEP_TRIES:
        _escalate(p, key, st, KIND.get(step.split(":")[0], "autopilot_failed"), f"{step} : {message}", step=step)
    p.save()
    return st.get("state") == "running"


def _escalate(p: Project, key: str, st: dict, kind: str, text: str, *, step: str,
              options: list[dict] | None = None, detail: dict | None = None) -> None:
    item = raise_attention(p, kind, text, costume=key, options=options, detail=detail)
    st.update(state="failed", failed={"step": step, "kind": kind, "id": item["id"], "at": now()})
    _log(st, f"Cal appelé : {item['title']}")


def _same_run(cos: dict, st: dict) -> bool:
    """L'étape travaille-t-elle encore pour le plein pied en cours ?"""
    return state(cos) is st and fullbody_token(cos) == st.get("fullbody")


def _real_chain() -> bool:
    return config.backend("portrait") != "stub"


def _no_fake(capability: str, what: str) -> None:
    if _real_chain() and config.backend(capability) == "stub":
        raise Hopeless(f"{what} tourne en factice (FACTORY_{capability.upper()}=stub) : l'autopilote ne valide pas "
                       f"un résultat factice dans une vraie chaîne")


def _apose(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    if arg == "gen":
        have = len(_round_candidates(cos, st)) - st.get("apose_base", 0)
        need = APOSE_BATCH * (st.get("apose_round", 0) + 1) - have
        _log(st, f"A-pose : propositions {have + 1} à {have + need} (tour {st.get('apose_round', 0) + 1}/{APOSE_ROUNDS})")
        chain.apose(p, key, variants=max(1, need), seed=h3.new_seed(), report=report)
        return
    _apose_pick(p, key, cos, st, report)


def _apose_pick(p: Project, key: str, cos: dict, st: dict, report) -> None:
    from . import apose_pick

    ap = apose_of(cos)
    body = p.path(cos["fullbody"]["validated"])
    cands = _round_candidates(cos, st)
    todo = [c for _, c in cands if (c.get("measure") or {}).get("fullbody") != st["fullbody"]]
    if todo:
        got = apose_pick.measure([body, *(p.path(c["file"]) for c in todo)],
                                 workdir=p.dir(f"costumes/{key}/apose/.measure"), report=report)
        ref = got[str(body)]
        for c in todo:
            m = apose_pick.score(body, ref, p.path(c["file"]), got[str(p.path(c["file"]))])
            c["measure"] = {**m, "engine": "dwpose", "fullbody": st["fullbody"], "at": now()}
            _log(st, f"A-pose {c['file'].rsplit('/', 1)[-1]} : {'recevable' if m['ok'] else 'refusée'}, "
                     f"note {m['score']}" + (f" — {'; '.join(m['fails'][:3])}" if m["fails"] else ""))
    if not _same_run(cos, st):
        return
    good = [(n, c) for n, c in cands if c["measure"]["ok"]]
    if good:
        n, best = max(good, key=lambda nc: nc[1]["measure"]["score"])
        metric = {k: best["measure"].get(k) for k in ("score", "arms_deg", "elbows_deg", "legs_deg", "ankle_ratio",
                                                      "facing", "nose_offset", "torso_tilt", "level", "knees_deg",
                                                      "ankle_level", "eye_tilt", "frame", "outfit", "engine")}
        metric["thresholds"] = apose_pick.THRESHOLDS
        metric["candidates"] = len(cands)
        p.validate_apose(key, str(n), by="auto", metric=metric)
        settle(p, key, ("apose_failed",))
        _log(st, f"A-pose n° {n} validée par la mesure (note {best['measure']['score']}, "
                 f"bras {best['measure']['arms_deg']})")
        return
    if st.get("apose_round", 0) + 1 < APOSE_ROUNDS:
        st["apose_round"] = st.get("apose_round", 0) + 1
        _log(st, f"aucune A-pose recevable : tour {st['apose_round'] + 1}/{APOSE_ROUNDS}")
        return
    ranked = sorted(cands, key=lambda nc: -nc[1]["measure"].get("score", 0.0))
    options = [{"action": "choose", "candidate": str(n), "file": c["file"], "score": c["measure"].get("score"),
                "fails": c["measure"].get("fails"), "label": f"prendre la n° {n}"} for n, c in ranked[:4]]
    options += [{"action": "retry", "label": "quatre de plus"}, {"action": "dismiss", "label": "ignorer"}]
    _escalate(p, key, st, "apose_failed",
              f"{len(cands)} A-poses tirées, aucune ne passe la mesure : choisis-en une, ou relance.",
              step="apose:pick", options=options)


def _views(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    names = [n for n in arg.split(",") if n]
    _log(st, f"vues {', '.join(names)}")
    chain.views(p, key, names=names, seed=h3.new_seed(), report=report)
    st["views_redo"] = [n for n in st.get("views_redo") or [] if n not in names]


def _prep(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    chain.prep(p, key, report=report)


def _check(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    v = cos["views"]
    if not v.get("check"):
        angles = {"left": 100.0} if _stub_fail("views", "portrait") else None
        res = chain.check(p, key, angles=angles, report=report)
    else:
        res = v["check"]
    table = {n: f"{t['value']:.1f}° ({t['error']:+.1f}°, {t['source']})" for n, t in res["angles"].items()}
    if res["ok"]:
        res["validated_by"] = "auto"
        settle(p, key, ("views_failed",))
        _log(st, "vues contrôlées : " + ", ".join(f"{n} {t}" for n, t in table.items()))
        return
    bad = sorted(res["errors"])
    if st.get("views_round", 0) < VIEW_ROUNDS:
        st["views_round"] = st.get("views_round", 0) + 1
        st["views_redo"] = bad
        _log(st, f"contrôle en échec ({', '.join(bad)}) : reprise {st['views_round']}/{VIEW_ROUNDS}")
        return
    _escalate(p, key, st, "views_failed",
              f"après {VIEW_ROUNDS} reprises, {', '.join(bad)} reste hors de ±{chain.TOLERANCE_DEG:g}°.",
              step="check", detail={"angles": res["angles"], "errors": res["errors"]})


MESH_MIN_TRIANGLES = 500     # un mesh vide ou en miettes ; TRELLIS.2 en rend 50 000
MESH_SPAN = (0.35, 1.4)     # envergure (x) / taille : A-pose, ni bras collés ni morceaux épars
MESH_DEPTH_MAX = 0.6        # profondeur (z) / taille


def _mesh(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    from . import mesh as mesh_mod

    _no_fake(mesh_mod.ENGINES[mesh_mod.DEFAULT_ENGINE], "le mesh")
    if _stub_fail("mesh", "trellis"):
        raise ChainError("échec simulé (FACTORY_STUB_FAIL=mesh)")
    check_at = cos["views"]["check"]["at"]
    _log(st, "mesh TRELLIS.2 multi-vues")
    entry = chain.mesh(p, key, report=report)
    entry["views_check"] = check_at
    s = entry.get("stats") or {}
    size = s.get("size_m") or [0, 0, 0]
    height = size[1] or 1e-6
    metric = {"triangles": s.get("triangles"), "size_m": size, "span": round(size[0] / height, 3),
              "depth": round(size[2] / height, 3), "textures": s.get("textures")}
    fails = []
    if (s.get("triangles") or 0) < MESH_MIN_TRIANGLES:
        fails.append(f"{s.get('triangles')} triangles")
    if not MESH_SPAN[0] <= metric["span"] <= MESH_SPAN[1]:
        fails.append(f"envergure {metric['span']} × la taille")
    if metric["depth"] > MESH_DEPTH_MAX:
        fails.append(f"profondeur {metric['depth']} × la taille")
    entry["validated_by"], entry["validated_metric"] = "auto", {**metric, "ok": not fails, "fails": fails}
    p.save()
    if fails:
        entry["rejected"] = True
        raise ChainError("mesh refusé par la mesure : " + "; ".join(fails))
    settle(p, key, ("mesh_failed",))
    _log(st, f"mesh v{entry['version']} validé par la mesure ({metric['triangles']} triangles, "
             f"envergure {metric['span']})")


def _rig(p: Project, key: str, cos: dict, st: dict, arg: str, report) -> None:
    from . import rigcheck
    from .cli_motion import rig

    _no_fake("unirig", "le rig (UniRig)")
    if _stub_fail("rig", "unirig"):
        raise ChainError("échec simulé (FACTORY_STUB_FAIL=rig)")
    mesh = _current_mesh(cos)
    entry = next((r for r in cos["rigs"] if r["mesh"] == mesh["version"] and r["verdict"] == "unseen"), None)
    if entry is None:
        _log(st, f"rig UniRig → SOMA sur le mesh v{mesh['version']}")
        entry = rig(p, key, mesh=mesh["version"], report=report)
    m = rigcheck.measure(p.path(entry["glb"]), entry["meta"])
    entry.update(verdict="accepted" if m["ok"] else "rejected", verdict_at=now(), validated_by="auto",
                 validated_metric=m)
    p.save()
    if not m["ok"]:
        raise ChainError("rig refusé par la mesure : " + "; ".join(m["fails"]))
    settle(p, key, ("rig_failed",))
    raise_attention(p, "rig_review", f"rig v{entry['version']} accepté sur mesure (bras {m['arm_angle_deg']}°, "
                                     f"accroupi −{m.get('squat_drop', 0):.0%}) : un coup d'œil si tu veux.",
                    costume=key, options=[{"action": "dismiss", "label": "vu"}], detail={"rig": entry["version"]})
    _log(st, f"rig v{entry['version']} accepté par la mesure")


STEPS = {"apose": _apose, "views": _views, "prep": _prep, "check": _check, "mesh": _mesh, "rig": _rig}


def pending(projects) -> list[tuple[str, str]]:
    """(slug, costume) des autopilotes à reprendre au démarrage du studio."""
    out = []
    for p in projects:
        for key, cos in p.data.get("costumes", {}).items():
            if (cos.get("autopilot") or {}).get("state") == "running":
                out.append((p.data["slug"], key))
    return out
