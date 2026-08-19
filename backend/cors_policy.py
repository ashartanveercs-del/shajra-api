"""Browser-origin policy for the public API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import Settings


ALLOWED_METHODS = ["DELETE", "GET", "POST", "PUT"]
ALLOWED_HEADERS = ["Authorization", "Content-Type", "X-Idempotency-Key"]


def configure_cors(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=False,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
    )
