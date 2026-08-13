from fastapi import APIRouter

# Endpoints implemented alongside documents (Section 5: list/create/delete collections).
router = APIRouter(prefix="/collections", tags=["collections"])
