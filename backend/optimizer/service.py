import logging
from datetime import datetime, timezone
from threading import Lock

from postgrest.exceptions import APIError

from .booking_repository import save_proposal
from .optimizer import optimize_desks, validate_proposal
from .supabase_connection import load_company_data


logger = logging.getLogger(__name__)

_company_locks = {}
_locks_guard = Lock()


def company_lock(company_id):
    company_id = str(company_id)

    with _locks_guard:
        if company_id not in _company_locks:
            _company_locks[company_id] = Lock()

        return _company_locks[company_id]


def can_recalculate(error):
    """Recalcular solo errores conocidos de propuesta desactualizada."""
    message = str(error)

    return any(
        text in message
        for text in (
            "Los datos cambiaron",
            "terminó durante el cálculo",
            "caducó durante el cálculo",
            "está bloqueada y no puede cambiar",
        )
    )


def optimize_company(company_id, client, max_attempts=3):
    """
    Leer, optimizar, validar y guardar.

    El bloqueo evita dos cálculos de la misma empresa
    dentro de este proceso de Python.
    """
    with company_lock(company_id):
        for attempt in range(max_attempts):
            spaces, requests, snapshot, _ = load_company_data(
                company_id=str(company_id),
                client=client,
            )

            if not requests:
                return {
                    "saved": True,
                    "updated": 0,
                    "served": 0,
                }

            now = datetime.now(timezone.utc)

            result = optimize_desks(
                spaces,
                requests,
                now=now,
            )

            validate_proposal(
                spaces,
                requests,
                result["assignments"],
                now=now,
            )

            try:
                saved = save_proposal(
                    client,
                    snapshot,
                    result["assignments"],
                )
            except APIError as error:
                if (
                    can_recalculate(error)
                    and attempt + 1 < max_attempts
                ):
                    # La siguiente iteración vuelve a leer todos los datos.
                    logger.info(
                        "Recalculando empresa %s por cambios concurrentes.",
                        company_id,
                    )
                    continue

                raise

            return {
                **saved,
                "served": result["served"],
                "changes": result["changes"],
            }

    raise RuntimeError("No se pudo completar la optimización.")


def try_optimize_company(company_id, client):
    """Un fallo del optimizador no elimina la solicitud registrada."""
    try:
        optimize_company(company_id, client)

        return {
            "completed": True,
        }

    except Exception:
        logger.exception(
            "Falló la optimización de la empresa %s.",
            company_id,
        )

        return {
            "completed": False,
            "message": (
                "La operación se registró, pero no se pudo completar "
                "la optimización."
            ),
        }