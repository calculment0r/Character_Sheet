"""Relais vers le modèle de texte local.

Le front peut parler au modèle de deux façons. Soit il l'appelle en
direct, et c'est alors au serveur d'inférence de poser les en-têtes
CORS ; soit il passe par ici, et dans ce cas le navigateur ne connaît
jamais l'adresse interne du modèle ni son jeton.

On ne réécrit pas le corps : le dialecte OpenAI entre et ressort tel
quel. Seule l'adresse et l'autorisation changent.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import settings

# Volontairement monté sur /v1 : le front pointe alors vers l'API
# exactement comme il pointerait vers le DGX, sans rien changer dans ses
# réglages. Une seule URL à connaître pour l'opérateur.
router = APIRouter(prefix="/v1", tags=["llm"])

TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


def _headers() -> dict[str, str]:
    h = {"content-type": "application/json"}
    if settings.llm_token:
        h["authorization"] = f"Bearer {settings.llm_token}"
    return h


def _base() -> str:
    url = settings.llm_url.rstrip("/")
    return url[:-3].rstrip("/") if url.endswith("/v1") else url


@router.get("/models")
async def models():
    if not settings.llm_url:
        raise HTTPException(503, "FACTORY_LLM_URL n'est pas configurée")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(f"{_base()}/v1/models", headers=_headers())
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"modèle injoignable : {exc}") from exc
    return JSONResponse(r.json(), status_code=r.status_code)


@router.post("/chat/completions")
async def chat(request: Request):
    if not settings.llm_url:
        raise HTTPException(503, "FACTORY_LLM_URL n'est pas configurée")

    payload = await request.json()
    # Le front n'impose pas le modèle : c'est le DGX qui décide de ce
    # qu'il sert. On écrase donc systématiquement le champ.
    payload["model"] = settings.llm_model

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.post(f"{_base()}/v1/chat/completions", json=payload, headers=_headers())
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"modèle injoignable : {exc}") from exc

    return JSONResponse(r.json(), status_code=r.status_code)
