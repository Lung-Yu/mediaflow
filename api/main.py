from contextlib import asynccontextmanager
from fastapi import FastAPI
from api import db
from api.reconcile import reconcile
from api.routes import events, files, status
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await reconcile()  # fill any gaps from missed events while API was down
    yield


app = FastAPI(title="mediaflow API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(events.router)
app.include_router(files.router)
app.include_router(status.router)


@app.get("/health")
def health():
    return {"status": "ok"}
