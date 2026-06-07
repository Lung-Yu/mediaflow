"""Pipeline status dashboard data."""
from fastapi import APIRouter
from api import db

router = APIRouter(prefix="/status")


@router.get("/")
async def overview():
    return await db.get_status_overview()
