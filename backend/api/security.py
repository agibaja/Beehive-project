import os

from fastapi import Depends, HTTPException
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from supabase import Client

from api.database import get_database


bearer = HTTPBearer(auto_error=False)


def get_current_employee(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    database: Client = Depends(get_database),
):
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Debes identificarte con Google.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    audience = os.getenv("GOOGLE_ADDON_CLIENT_ID")

    if not audience:
        raise HTTPException(
            status_code=503,
            detail="Falta configurar GOOGLE_ADDON_CLIENT_ID en .env.",
        )

    try:
        claims = id_token.verify_oauth2_token(
            credentials.credentials,
            Request(),
            audience=audience,
        )
    except ValueError as exc:
        print("Error al verificar identidad Google:", str(exc))
        raise HTTPException(
            status_code=401,
            detail="Identidad de Google inválida o caducada.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except TransportError:
        raise HTTPException(
            status_code=503,
            detail="No se pudo verificar la identidad con Google.",
        )

    google_sub = claims.get("sub")

    if not google_sub:
        raise HTTPException(
            status_code=401,
            detail="El token no identifica a un usuario.",
        )

    employees = (
        database.table("employee")
        .select("id, company_id, name, role")
        .eq("google_sub", google_sub)
        .limit(1)
        .execute()
        .data
    )

    if not employees:
        raise HTTPException(
            status_code=403,
            detail="Tu cuenta Google no está vinculada a un empleado.",
        )

    employee = employees[0]

    if not employee.get("company_id"):
        raise HTTPException(
            status_code=403,
            detail="El empleado no tiene una empresa asignada.",
        )

    return employee


def require_admin(employee=Depends(get_current_employee)):
    if employee.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Esta operación requiere permisos de administrador.",
        )

    return employee