"""Lancer un modèle sur un DGX, par ssh.

Kimodo et UniRig n'ont pas de nœud ComfyUI : chacun vit dans son venv
sur une des machines (leurs dépendances se contredisent). La chaîne y
dépose ses fichiers, lance le script d'entrée `tools/remote/<nom>_entry.py`
dans ce venv, et rapatrie le résultat. Le modèle se recharge à chaque
appel : c'est plus lent que ComfyUI, mais sans conflit.

Réglages par capacité, dans factory.local.json ou en FACTORY_… :

    remote_<capacité>   l'hôte ssh, par exemple "dgx2"
    python_<capacité>   le python du venv, "/home/dgx/kimodo/.venv/bin/python"
    cwd_<capacité>      le dossier d'où lancer (le dépôt du modèle), facultatif

DGX1 et DGX2 sont des clones, clés d'hôte comprises : ssh ne sait pas
les distinguer. Quand l'hôte porte le nom d'une machine, on vérifie
donc `hostname` avant de lancer quoi que ce soit.
"""

from __future__ import annotations

import shlex
import subprocess
import uuid
from pathlib import Path

from . import config
from .project import ChainError

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=15"]


def _ssh(host: str, command: str, *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *SSH_OPTS, host, command], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _scp(src: str, dest: str, *, timeout: float = 600) -> None:
    res = subprocess.run(["scp", "-q", *SSH_OPTS, src, dest], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=timeout)
    if res.returncode:
        raise ChainError(f"copie {src} → {dest} impossible : {res.stderr.strip()[-500:]}")


def target(capability: str) -> tuple[str, str, str]:
    host = config.setting(f"remote_{capability}")
    python = config.setting(f"python_{capability}")
    if not host or not python:
        raise ChainError(f"{capability} : règle remote_{capability} (hôte ssh) et python_{capability} (python du "
                         f"venv du modèle) dans factory.local.json")
    return host, python, config.setting(f"cwd_{capability}")


def run(capability: str, script: Path, *, args: list[str], inputs: dict[str, Path], outputs: list[str],
        workdir: Path, report=lambda p, m: None, timeout: float = 3600) -> dict[str, Path]:
    """Copie `script` et `inputs` dans un dossier temporaire de l'hôte,
    lance le script, rapatrie `outputs` dans `workdir`. Dans `args`,
    `{job}` désigne ce dossier temporaire."""
    host, python, cwd = target(capability)
    job = f"/tmp/usine-{capability}-{uuid.uuid4().hex[:8]}"
    res = _ssh(host, f"hostname && mkdir -p {job}", timeout=60)
    if res.returncode:
        raise ChainError(f"{host} injoignable : {res.stderr.strip()[-500:]}")
    seen = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "?"
    if host.lower().startswith("dgx") and seen.lower() != host.lower():
        raise ChainError(f"l'alias {host} répond {seen} : les DGX sont des clones, vérifie ~/.ssh/config")
    report(0.05, f"{capability} sur {seen}")
    try:
        for f in [script, *inputs.values()]:
            _scp(str(f), f"{host}:{job}/")
        for name, f in inputs.items():
            if Path(f).name != name:
                _ssh(host, f"mv {job}/{shlex.quote(Path(f).name)} {job}/{shlex.quote(name)}", timeout=60)
        line = " ".join([python, f"{job}/{script.name}", *(shlex.quote(a.replace("{job}", job)) for a in args)])
        report(0.1, f"{capability} tourne")
        res = _ssh(host, f"cd {shlex.quote(cwd or job)} && {line}", timeout=timeout)
        (workdir.mkdir(parents=True, exist_ok=True))
        (workdir / f"{capability}.log").write_text(res.stdout + "\n--- stderr ---\n" + res.stderr, encoding="utf-8")
        if res.returncode:
            raise ChainError(f"{capability} a échoué sur {seen} (code {res.returncode}) : "
                             f"{res.stderr.strip()[-1500:] or res.stdout.strip()[-1500:]}")
        got = {}
        for name in outputs:
            _scp(f"{host}:{job}/{name}", str(workdir / name))
            got[name] = workdir / name
        report(0.9, f"{capability} : résultats rapatriés")
        return got
    finally:
        _ssh(host, f"rm -rf {job}", timeout=60)
