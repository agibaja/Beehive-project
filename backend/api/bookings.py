import logging

from optimizer.service import try_optimize_company
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from api.database import get_database
from api.schemas import BookingCreate
from api.security import get_current_employee
from postgrest.exceptions import APIError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bookings", tags=["Reservas"])


def booking_with_space(database, booking):
    result = dict(booking)
    result["space_name"] = None

    if booking.get("space_id"):
        try:
            spaces = (
                database.table("space")
                .select("name")
                .eq("id", booking["space_id"])
                .limit(1)
                .execute()
                .data
            )

            if spaces:
                result["space_name"] = spaces[0]["name"]

        except Exception:
            # El nombre es informativo: no invalida un guardado correcto.
            logger.exception("No se pudo consultar el nombre del espacio.")

    return result


@router.post("", status_code=201)
def create_booking(
    payload: BookingCreate,
    employee=Depends(get_current_employee),
    database: Client = Depends(get_database),
):
    now = datetime.now(timezone.utc)

    if payload.start_time <= now:
        raise HTTPException(
            status_code=422,
            detail="La solicitud ya ha alcanzado su hora de inicio.",
        )

    booking_id = str(payload.request_id or uuid4())

    values = payload.model_dump(
        mode="json",
        exclude={"request_id"},
    )

    def find_existing():
        return (
            database.table("booking")
            .select("*")
            .eq("id", booking_id)
            .eq("employee_id", employee["id"])
            .limit(1)
            .execute()
            .data
        )

    existing = find_existing()

    if not existing:
        row = {
            **values,
            "id": booking_id,
            "employee_id": employee["id"],
            "space_id": None,
            "status": "pending",
            "is_locked": False,
            "created_at": now.isoformat(),

            # Destino previsto: calendario principal del trabajador.
            "calendar_id": "primary",
            "calendar_event_id": (
                "beehive" + UUID(booking_id).hex
            ),
        }

        try:
            existing = (
                database.table("booking")
                .insert(row)
                .execute()
                .data
            )
        except APIError as error:
            if error.code != "23505":
                raise

            # Otro envío pudo insertar la misma solicitud.
            existing = find_existing()

            if not existing:
                raise HTTPException(
                    status_code=409,
                    detail="Identificador de solicitud no disponible.",
                )

    if not existing:
        raise HTTPException(
            status_code=502,
            detail=(
                "No se recibió confirmación. "
                "Reintenta desde el mismo formulario."
            ),
        )

    registered = existing[0]

    # Un identificador no puede reutilizarse con requisitos diferentes.
    for field, value in values.items():
        stored = registered.get(field)

        if field in ("start_time", "end_time"):
            same = (
                datetime.fromisoformat(
                    stored.replace("Z", "+00:00")
                )
                == datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )
            )
        else:
            same = stored == value

        if not same:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Este formulario ya registró otra solicitud. "
                    "Abre «Nueva solicitud» para cambiar los datos."
                ),
            )

    optimization = {"completed": True}

    if registered["status"] in ("pending", "assigned"):
        optimization = try_optimize_company(
            employee["company_id"],
            database,
        )

    try:
        refreshed = find_existing()

        if not refreshed:
            raise RuntimeError("No se encontró la reserva registrada.")

        booking = booking_with_space(database, refreshed[0])
        status_verified = True

    except Exception:
        logger.exception(
            "No se pudo consultar el estado final de la reserva."
        )

        booking = dict(registered)
        booking["space_name"] = None
        status_verified = False

    return {
        **booking,
        "optimization": optimization,
        "status_verified": status_verified,
    }


@router.get("")
def list_my_bookings(
    employee=Depends(get_current_employee),
    database: Client = Depends(get_database),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    rows = (
        database.table("booking")
        .select("*")
        .eq("employee_id", employee["id"])
        .order("created_at", desc=True)
        .order("id")
        .range(offset, offset + limit - 1)
        .execute()
        .data
    )

    return {
        "items": rows,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{booking_id}")
def get_my_booking(
    booking_id: UUID,
    employee=Depends(get_current_employee),
    database: Client = Depends(get_database),
):
    rows = (
        database.table("booking")
        .select("*")
        .eq("id", str(booking_id))
        .eq("employee_id", employee["id"])
        .limit(1)
        .execute()
        .data
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Reserva no encontrada.",
        )

    return booking_with_space(database, rows[0])


@router.post("/{booking_id}/cancel")
def cancel_my_booking(
    booking_id: UUID,
    employee=Depends(get_current_employee),
    database: Client = Depends(get_database),
):
    now = datetime.now(timezone.utc)

    # Una sola actualización condicional.
    # El bloqueo impide traslados, pero permite cancelar una reserva propia.
    rows = (
        database.table("booking")
        .update({"status": "cancelled"})
        .eq("id", str(booking_id))
        .eq("employee_id", employee["id"])
        .in_("status", ["pending", "assigned"])
        .gt("start_time", now.isoformat())
        .execute()
        .data
    )

    if rows:
        optimization = try_optimize_company(
            employee["company_id"],
            database,
        )

        return {
            **rows[0],
            "optimization": optimization,
        }

    existing = (
        database.table("booking")
        .select("*")
        .eq("id", str(booking_id))
        .eq("employee_id", employee["id"])
        .limit(1)
        .execute()
        .data
    )

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Reserva no encontrada.",
        )

    # Repetir una cancelación ya completada devuelve el mismo resultado.
    if existing[0]["status"] == "cancelled":
        return existing[0]

    raise HTTPException(
        status_code=409,
        detail="La reserva ya comenzó o su estado no permite cancelarla.",
    )