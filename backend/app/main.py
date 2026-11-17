import logging

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import router as api_router
from app.config import get_settings
from app.db import get_session
from app.log import configure_logging

configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="Job Platform API", version="0.1.0")
app.include_router(api_router)


@app.get("/health")
def health(session: Session = Depends(get_session)) -> JSONResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("health check: database unreachable")
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})
