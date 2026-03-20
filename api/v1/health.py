"""Health check endpoint."""
from fastapi import APIRouter
from core.config import settings

router = APIRouter()

@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": "2.0.0", "env": settings.env}
