from contextlib import asynccontextmanager

from fastapi import FastAPI

from db.connection import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="DeepDive", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}