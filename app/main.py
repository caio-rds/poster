import secrets
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from bson import ObjectId
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
from pymongo.errors import PyMongoError

from app.config import Settings, client_settings, get_settings
from app.cors import cors_options
from app.database import create_client
from app.images import download_jpeg
from app.models import PostOut, PostPage, PostStatus, StatusUpdate
from app.repository import PostRepository, public_post
from app.runtime import BackgroundWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = getattr(app.state, "settings", None) or get_settings()
    profiles = {name: client_settings(settings, name) for name in ("zion", "corte")}
    if not settings.api_key.get_secret_value():
        raise RuntimeError("Defina API_KEY no .env para iniciar a API")
    client = create_client(settings)
    worker = BackgroundWorker(settings)
    try:
        db = client[settings.mongodb_database]
        app.state.repository = PostRepository(db)
        app.state.repositories = {
            name: PostRepository(client[profile.mongodb_database])
            for name, profile in profiles.items()
        }
        app.state.worker = worker
        worker.start()
        yield
    finally:
        await worker.stop()
        await client.close()


api_key_header = APIKeyHeader(
    name="apikey", auto_error=False, description="Informe a API_KEY configurada no .env"
)


async def authenticate(request: Request, apikey: Annotated[str | None, Security(api_key_header)] = None):
    expected = request_settings(request).api_key.get_secret_value()
    if not expected:
        raise HTTPException(status_code=503, detail="API_KEY não configurada")
    if not secrets.compare_digest((apikey or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="API key inválida")


def request_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    return client_settings(settings, selected_client(request))


def selected_client(request: Request) -> str:
    client = request.path_params.get("client", "zion")
    if client not in {"zion", "corte"}:
        raise HTTPException(404, "Cliente não encontrado")
    return client


def repository(request: Request) -> PostRepository:
    client = selected_client(request)
    if "client" not in request.path_params:
        return request.app.state.repository
    return request.app.state.repositories[client]


def serialize(document: dict, request: Request) -> PostOut:
    prefix = f"/{selected_client(request)}" if "client" in request.path_params else ""
    return public_post(document, str(request.base_url), settings=request_settings(request), route_prefix=prefix)


Repo = Annotated[PostRepository, Depends(repository)]
router = APIRouter(dependencies=[Depends(selected_client), Depends(authenticate)])


async def database_error(request: Request, exc: PyMongoError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": "MongoDB indisponível"})


async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/posts/next", response_model=PostOut | None)
async def next_post(repo: Repo, request: Request):
    document = await repo.next_post()
    if document is None:
        return None
    return serialize(document, request)


@router.get("/posts", response_model=PostPage)
async def list_posts(
    repo: Repo,
    request: Request,
    status: PostStatus | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    documents, total = await repo.list_posts(status, offset, limit)
    return PostPage(
        items=[serialize(document, request) for document in documents],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.patch("/posts/{post_id}/status", response_model=PostOut)
async def update_status(post_id: str, body: StatusUpdate, repo: Repo, request: Request):
    if not ObjectId.is_valid(post_id):
        raise HTTPException(status_code=422, detail="ID de post inválido")
    document = await repo.mark_posted(ObjectId(post_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Post não encontrado")
    return serialize(document, request)


async def post_image(post_id: str, repo: Repo, request: Request):
    if not ObjectId.is_valid(post_id):
        raise HTTPException(422, "ID de post inválido")
    document = await repo.image_post(ObjectId(post_id))
    if document is None:
        raise HTTPException(404, "Post não encontrado")
    url = document.get("image_url") or request_settings(request).default_post_image_url
    if not url:
        raise HTTPException(404, "Post sem imagem")
    content = await repo.cached_image(url)
    if content is None:
        content = await download_jpeg(url)
        await repo.cache_image(url, content)
    return Response(
        content, media_type="image/jpeg",
        headers={
            "Content-Disposition": f'inline; filename="{post_id}.jpg"',
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_app(settings: Settings | None = None, *, enable_cors=True) -> FastAPI:
    api = FastAPI(title="Zion & Côrte Posts API", version="0.2.0", lifespan=lifespan)
    if settings is not None:
        api.state.settings = settings
    api.add_api_route("/health", health, methods=["GET"], tags=["health"])
    api.add_api_route("/health", health, methods=["HEAD"], include_in_schema=False)
    api.include_router(router)
    api.include_router(router, prefix="/{client}", tags=["clients"])
    api.add_api_route(
        "/{client}/posts/{post_id}/image.jpg", post_image, methods=["GET"], tags=["images"]
    )
    api.add_api_route(
        "/posts/{post_id}/image.jpg", post_image, methods=["GET"], tags=["images"]
    )
    api.add_api_route(
        "/posts/{post_id}/image.jpg", post_image, methods=["HEAD"], include_in_schema=False
    )
    api.add_api_route(
        "/{client}/posts/{post_id}/image.jpg", post_image, methods=["HEAD"], include_in_schema=False
    )
    api.add_exception_handler(PyMongoError, database_error)
    if enable_cors:
        api.add_middleware(CORSMiddleware, **cors_options(settings or get_settings()))
    return api


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
