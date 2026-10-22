"""Diagnóstico sem exibir segredos: python -m app.check."""

import asyncio

import httpx
from openai import AsyncOpenAI

from app.collectors import USER_AGENT, WordPressCollector
from app.config import client_settings, get_settings
from app.database import create_client
from app.errors import failure_hint


async def check():
    settings = get_settings()
    profiles = [client_settings(settings, name) for name in ("zion", "corte")]
    ok = True
    if settings.api_key.get_secret_value():
        print("API_KEY: configurada")
    else:
        print("API_KEY: ausente; a API não pode iniciar")
        ok = False
    mongo = create_client(settings)
    try:
        await mongo.admin.command("ping")
        for profile in profiles:
            db = mongo[profile.mongodb_database]
            count = await db.posts.count_documents({"status": "ready"})
            print(f"MongoDB {profile.client_slug}: database={profile.mongodb_database}; prontos={count}")
    except Exception as exc:
        print(failure_hint(exc))
        ok = False
    finally:
        await mongo.close()
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for profile in profiles:
            try:
                collector = WordPressCollector(client, profile)
                source = await anext(collector.collect())
                print(f"Blog {profile.client_slug}: artigo coletado; imagem={bool(source.image_url)}")
            except Exception as exc:
                print(f"{profile.client_slug}: {failure_hint(exc)}")
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
    for profile in profiles:
        if not profile.instagram_user_id or not profile.instagram_access_token.get_secret_value():
            print(f"Instagram {profile.client_slug}: credenciais ausentes; apenas blog habilitado")
            if profile.instagram_required:
                ok = False
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
