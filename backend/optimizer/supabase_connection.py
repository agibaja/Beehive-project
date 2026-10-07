import os
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client


def fetch_all(client, table):
    """Lee todas las filas por páginas, ordenadas por ID."""
    rows = []
    offset = 0
    page_size = 500

    while True:
        page = (
            client.table(table)
            .select("*")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )

        if not page:
            break

        rows.extend(page)
        offset += len(page)

    return rows


def parse_date(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if date.tzinfo is None:
        raise ValueError("Las fechas deben incluir zona horaria.")

    return date.astimezone(timezone.utc)

def load_active_bookings(client, employee_ids, now):
    employee_ids = list(employee_ids)

    if not employee_ids:
        return []

    # Actualizar las pendientes caducadas directamente en Supabase.
    (
        client.table("booking")
        .update({"status": "eliminado"})
        .in_("employee_id", employee_ids)
        .eq("status", "pending")
        .lte("start_time", now.isoformat())
        .execute()
    )

    # Descargar únicamente reservas activas que no han terminado.
    rows = []
    offset = 0
    page_size = 500

    while True:
        page = (
            client.table("booking")
            .select("*")
            .in_("employee_id", employee_ids)
            .in_("status", ["pending", "assigned"])
            .gt("end_time", now.isoformat())
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        )

        if not page:
            break

        rows.extend(page)
        offset += len(page)

    return rows

def load_company_data(company_id=None, client=None):
    load_dotenv(Path(__file__).with_name(".env"))

    # La API pasa la empresa del usuario autenticado.
    # COMPANY_ID queda como alternativa para la ejecución manual.
    if company_id is None:
        company_id = os.getenv("COMPANY_ID")

    if not company_id:
        raise ValueError("Falta indicar la empresa.")

    if client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")

        if not url or not key:
            raise ValueError(
                "Completa SUPABASE_URL y SUPABASE_KEY en .env."
            )

        client = create_client(url, key)

    now = datetime.now(timezone.utc)

    # Caducar las pendientes de esta empresa.
    employees = (
        client.table("employee")
        .select("id")
        .eq("company_id", company_id)
        .execute()
        .data
    )

    employee_ids = {row["id"] for row in employees}

    # Reutilizamos tu función para realizar la actualización.
    # Su descarga posterior es innecesaria aquí; abajo usamos la RPC.
    load_active_bookings(client, employee_ids, now)

    # Una sola consulta proporciona la copia que usaremos al guardar.
    snapshot = (
        client.rpc(
            "optimizer_snapshot",
            {
                "p_company_id": company_id,
                "p_cutoff": now.isoformat(),
            },
        )
        .execute()
        .data
    )

    spaces = snapshot["spaces"]
    employees = snapshot["employees"]
    employee_ids = {row["id"] for row in employees}
    spaces_by_id = {row["id"]: row for row in spaces}

    if not spaces or not employees:
        raise ValueError(
            "No se encontraron espacios o empleados para COMPANY_ID."
        )

    requests = []
    inferred_types = 0

    for row in snapshot["bookings"]:
        if row["employee_id"] not in employee_ids:
            continue

        if row["status"] not in ("pending", "assigned"):
            continue

        request = dict(row)
        request["start_time"] = parse_date(row["start_time"])
        request["end_time"] = parse_date(row["end_time"])
        request["created_at"] = parse_date(row["created_at"])

        # Excluir reservas terminadas.
        if request["end_time"] <= now:
            continue

        # No asignar solicitudes pendientes cuyo inicio ya pasó.
        if (
            request["status"] == "pending"
            and request["start_time"] <= now
        ):
            continue

        current_space_id = request.get("space_id")

        if (
            current_space_id is not None
            and current_space_id not in spaces_by_id
        ):
            raise ValueError(
                f"Reserva {request['id']}: espacio fuera de la empresa "
                "o inexistente."
            )

        request_type = request.get("request_type")

        # Compatibilidad con reservas antiguas ya asignadas:
        # podemos conocer el tipo mediante su espacio actual.
        if not request_type and current_space_id is not None:
            request_type = spaces_by_id[current_space_id]["type"]
            inferred_types += 1

        if request_type not in ("desk", "meeting_room"):
            raise ValueError(
                f"Reserva {request['id']}: completa request_type "
                "con desk o meeting_room en Supabase."
            )

        request["request_type"] = request_type

        # Sin preferencia declarada, no damos prioridad a la ventana.
        if request.get("prefers_window") is None:
            request["prefers_window"] = False

        requests.append(request)

    print(f"Espacios cargados: {len(spaces)}")
    print(f"Reservas activas cargadas: {len(requests)}")

    if inferred_types:
        print(
            f"Tipo deducido del espacio actual en "
            f"{inferred_types} reservas antiguas."
        )

    return spaces, requests, snapshot, client