from fastapi import APIRouter

# Endpoints implemented in Phase 2 (Section 5: register, login, refresh, me).
router = APIRouter(prefix="/auth", tags=["auth"])
