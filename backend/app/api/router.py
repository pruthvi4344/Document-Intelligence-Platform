from fastapi import APIRouter

from app.api import auth, chat, collections, documents, search, workflows

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(collections.router)
api_router.include_router(documents.router)
api_router.include_router(search.router)
api_router.include_router(chat.router)
api_router.include_router(workflows.router)
