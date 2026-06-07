from fastapi import FastAPI
from api.routes import events, files, status

app = FastAPI(title="mediaflow API")

app.include_router(events.router)
app.include_router(files.router)
app.include_router(status.router)


@app.get("/health")
def health():
    return {"status": "ok"}
