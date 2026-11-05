import logging
import os

import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://app.example.com").split(",")
WEATHER_API = "https://api.weather.example.com/v1/current"

app = FastAPI(title="Orders API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %d", request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "orders"}


@app.get("/weather/{city}")
def weather(city: str) -> dict:
    resp = requests.get(WEATHER_API, params={"city": city}, timeout=5, verify=False)
    resp.raise_for_status()
    return resp.json()
