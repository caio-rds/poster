import secrets
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from bson import ObjectId
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pymongo.errors import PyMongoError

from app.config import Settings, get_settings
from app.cors import cors_options
from app.database import create_client
from app.models import PostOut, PostPage, PostStatus, StatusUpdate
from app.repository import PostRepository, public_post
from app.runtime import BackgroundWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.api_key.get_secret_value():
        raise RuntimeError("Defina API_KEY no .env para iniciar a API")
    client = create_client(settings)
    worker = BackgroundWorker(settings)
    try:
        db = client[settings.mongodb_database]
        app.state.repository = PostRepository(db)
        app.state.worker = worker
        worker.start()
        yield
    finally:
        await worker.stop()
        await client.close()


api_key_header = APIKeyHeader(
    name="apikey", auto_error=False, description="Informe a API_KEY configurada no .env"
)


async def authenticate(apikey: Annotated[str | None, Security(api_key_header)] = None):
    expected = get_settings().api_key.get_secret_value()
    if not expected:
        raise HTTPException(status_code=503, detail="API_KEY não configurada")
    if not secrets.compare_digest((apikey or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="API key inválida")


def repository(request: Request) -> PostRepository:
    return request.app.state.repository


Repo = Annotated[PostRepository, Depends(repository)]
router = APIRouter(dependencies=[Depends(authenticate)])


async def database_error(request: Request, exc: PyMongoError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": "MongoDB indisponível"})


@router.get("/posts/next", response_model=PostOut | None)
async def next_post(repo: Repo):
    document = await repo.next_post()
    if document is None:
        return None
    return public_post(document)


@router.get("/posts", response_model=PostPage)
async def list_posts(
    repo: Repo,
    status: PostStatus | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    documents, total = await repo.list_posts(status, offset, limit)
    return PostPage(
        items=[public_post(document) for document in documents],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.patch("/posts/{post_id}/status", response_model=PostOut)
async def update_status(post_id: str, body: StatusUpdate, repo: Repo):
    if not ObjectId.is_valid(post_id):
        raise HTTPException(status_code=422, detail="ID de post inválido")
    document = await repo.mark_posted(ObjectId(post_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Post não encontrado")
    return public_post(document)


def create_app(settings: Settings | None = None, *, enable_cors=True) -> FastAPI:
    api = FastAPI(title="Zion Posts API", version="0.1.0", lifespan=lifespan)
    api.include_router(router)
    api.add_exception_handler(PyMongoError, database_error)
    if enable_cors:
        api.add_middleware(CORSMiddleware, **cors_options(settings or get_settings()))
    return api


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
