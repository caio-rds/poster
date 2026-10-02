"""Diagnóstico sem exibir segredos: python -m app.check."""

import asyncio

import httpx
from openai import AsyncOpenAI

from app.collectors import SITE, USER_AGENT, WebsiteCollector, extract_source
from app.config import get_settings
from app.database import create_client
from app.errors import failure_hint


async def check():
    settings = get_settings()
    ok = True
    if settings.api_key.get_secret_value():
        print("API_KEY: configurada")
    else:
        print("API_KEY: ausente; a API não pode iniciar")
        ok = False
    mongo = create_client(settings)
    try:
        await mongo.admin.command("ping")
        db = mongo[settings.mongodb_database]
        count = await db.posts.count_documents({"status": "ready"})
        print(f"MongoDB: conectado; database={settings.mongodb_database}; posts prontos={count}")
    except Exception as exc:
        print(failure_hint(exc))
        ok = False
    finally:
        await mongo.close()
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        try:
            collector = WebsiteCollector(client, settings)
            await collector.initialize()
            html = await collector.fetch(f"{SITE}/")
            extract_source(html, f"{SITE}/", "website")
            print("Site Zion: robots.txt verificado; página inicial coletada com conteúdo útil")
        except Exception as exc:
            print(failure_hint(exc))
            ok = False
    key = settings.openai_api_key.get_secret_value()
    if not key:
        print("OPENAI_API_KEY: ausente; o worker não pode gerar posts")
        ok = False
    else:
        async with AsyncOpenAI(api_key=key, timeout=15, max_retries=0) as client:
            try:
                models = {model.id for model in (await client.models.list()).data}
                for model in (settings.openai_model, settings.openai_embedding_model):
                    if model in models:
                        print(f"OpenAI: modelo disponível: {model}")
                    else:
                        print(f"OpenAI: modelo não listado para essa chave: {model}")
                        ok = False
            except Exception as exc:
                print(failure_hint(exc))
                ok = False
    if not settings.instagram_user_id or not settings.instagram_access_token.get_secret_value():
        print("Instagram: credenciais ausentes; só site/blog estão habilitados")
        if settings.instagram_required:
            ok = False
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
