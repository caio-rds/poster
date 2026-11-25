"""Diagnóstico sem exibir segredos: python -m app.check."""

import asyncio

import httpx
from openai import AsyncOpenAI

from app.collectors import USER_AGENT, WordPressCollector
from app.config import client_settings, get_settings
from app.corte import download_corte_image, editorial_posts
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
            print(
                f"MongoDB {profile.client_slug}: banco={profile.mongodb_database}, prontos={count}"
            )
    except Exception as exc:
        print(failure_hint(exc))
        ok = False
    finally:
        await mongo.close()
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        try:
            collector = WordPressCollector(client, settings)
            source = await anext(collector.collect())
            print(f"Blog Zion: artigo coletado; imagem={bool(source.image_url)}")
        except Exception as exc:
            print(f"Zion: {failure_hint(exc)}")
            ok = False
    try:
        posts = editorial_posts()
        await download_corte_image(posts[0].file_id)
        print(f"Drive Côrte: {len(posts)} pares de foto e legenda; download verificado")
    except Exception as exc:
        print(f"Drive Côrte: {failure_hint(exc)}")
        ok = False
    key = settings.openai_api_key.get_secret_value()
    if not key:
        print("OPENAI_API_KEY: ausente; o worker Zion não pode gerar posts")
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
        print("Instagram Zion: credenciais ausentes; apenas blog habilitado")
        if settings.instagram_required:
            ok = False
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
