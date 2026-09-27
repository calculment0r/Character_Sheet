"""Un certificat auto-signé, pour que le navigateur prête son micro.

Le navigateur ne donne le micro qu'à une page en contexte sûr : HTTPS,
ou `localhost`. Le studio et le service vocal servent en HTTP sur le
réseau local ; pour parler au personnage depuis le PC, ils ouvrent aussi
un port HTTPS avec ce certificat. Le navigateur prévient une fois
(certificat inconnu) : l'accepter, pour cette adresse. Rien n'est
installé dans le système : le certificat vit dans
`~/.cache/character-factory/tls/`, fait par `openssl`.

Sans HTTPS, l'autre voie : un tunnel ssh vers `localhost`, qui est
toujours un contexte sûr (voir docs/VOIX.md).
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import subprocess
from pathlib import Path

from .project import ChainError

TLS_DIR = Path.home() / ".cache" / "character-factory" / "tls"


def _names() -> list[str]:
    names = {"localhost", "127.0.0.1", socket.gethostname()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            names.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.10.1", 9))
            names.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(names)


def ensure_cert(extra: list[str] | None = None) -> tuple[Path, Path]:
    """Le certificat de cette machine (fait une fois), et sa clé."""
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    crt, key = TLS_DIR / "cert.pem", TLS_DIR / "key.pem"
    if crt.is_file() and key.is_file():
        return crt, key
    names = _names() + list(extra or [])
    san = []
    for n in names:
        try:
            ipaddress.ip_address(n)
            san.append(f"IP:{n}")
        except ValueError:
            san.append(f"DNS:{n}")
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
           "-keyout", str(key), "-out", str(crt), "-subj", "/CN=character-factory",
           "-addext", "subjectAltName=" + ",".join(san)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ChainError(f"certificat auto-signé impossible (openssl) : {exc}") from exc
    return crt, key


def server_context() -> ssl.SSLContext:
    crt, key = ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    return ctx
