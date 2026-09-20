"""Réglages, lus dans l'environnement.

Rien n'a de valeur par défaut secrète. Sans Redis ni MinIO le service
démarre quand même, en mémoire et sans stockage objet : c'est le mode
de mise au point, il perd tout au redémarrage et le dit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # Qui a le droit d'appeler. Vide = ouvert, et le service prévient.
    tokens: list[str] = field(default_factory=lambda: _list("FACTORY_TOKENS"))

    # Origines autorisées pour le front statique. GitHub Pages sert sur
    # une origine différente de l'API : sans ça le navigateur refuse.
    # Les adresses locales sont là pour la mise au point ; en production
    # on pose FACTORY_CORS_ORIGINS et elles disparaissent.
    cors_origins: list[str] = field(
        default_factory=lambda: _list(
            "FACTORY_CORS_ORIGINS",
            "https://calculment0r.github.io,http://localhost:8811,http://127.0.0.1:8811",
        )
    )

    # File de travaux. Absente, on exécute en tâche asyncio locale.
    redis_url: str = os.getenv("FACTORY_REDIS_URL", "")

    # Stockage objet. Absent, les artefacts restent sur le disque local
    # et sont servis par l'API elle-même.
    minio_endpoint: str = os.getenv("FACTORY_MINIO_ENDPOINT", "")
    minio_key: str = os.getenv("FACTORY_MINIO_KEY", "")
    minio_secret: str = os.getenv("FACTORY_MINIO_SECRET", "")
    minio_bucket: str = os.getenv("FACTORY_MINIO_BUCKET", "factory")
    minio_secure: bool = os.getenv("FACTORY_MINIO_SECURE", "1") != "0"
    presign_seconds: int = int(os.getenv("FACTORY_PRESIGN_SECONDS", "3600"))

    # Le modèle de texte local, servi en dialecte OpenAI.
    llm_url: str = os.getenv("FACTORY_LLM_URL", "")
    llm_model: str = os.getenv("FACTORY_LLM_MODEL", "local-model")
    llm_token: str = os.getenv("FACTORY_LLM_TOKEN", "")

    # Où tombent les artefacts quand MinIO n'est pas là.
    local_store: str = os.getenv("FACTORY_LOCAL_STORE", "./_artifacts")

    # Étiquette de ce nœud. Deux DGX partagent la file, pas l'étiquette.
    node: str = os.getenv("FACTORY_NODE", "dgx-01")

    @property
    def has_redis(self) -> bool:
        return bool(self.redis_url)

    @property
    def has_minio(self) -> bool:
        return bool(self.minio_endpoint and self.minio_key and self.minio_secret)

    @property
    def open_access(self) -> bool:
        return not self.tokens


settings = Settings()
