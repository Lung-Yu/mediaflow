"""Pipeline status dashboard data."""
from fastapi import APIRouter

router = APIRouter(prefix="/status")


@router.get("/")
def overview():
    # TODO: query pipeline.db
    return {"processing": [], "queue": [], "recent": []}
