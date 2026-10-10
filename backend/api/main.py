import logging

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from api.bookings import router as bookings_router
from api.admin import router as admin_router
from api.security import get_current_employee


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Beehive Backend",
    version="0.1.0",
)

app.include_router(bookings_router)
app.include_router(admin_router)

@app.exception_handler(APIError)
async def database_error_handler(request: Request, exc: APIError):
    logger.error(
        "Error de base de datos en %s: código %s",
        request.url.path,
        exc.code,
    )

    return JSONResponse(
        status_code=503,
        content={
            "detail": "No se pudo completar la operación en la base de datos."
        },
    )


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok"}


@app.get("/me", tags=["Usuario"])
def me(employee=Depends(get_current_employee)):
    return employee