from fastapi import APIRouter

# Endpoints implemented in Phase 3 (Section 6: sessions, SSE-streamed messages with citations).
router = APIRouter(prefix="/chat", tags=["chat"])
