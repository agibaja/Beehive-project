from datetime import datetime, timedelta, timezone
from ortools.sat.python import cp_model


def overlap(request_a, request_b):
    """Intervalos [inicio, fin): pueden compartir el instante de cambio."""
    return (
        request_a["start_time"] < request_b["end_time"]
        and request_b["start_time"] < request_a["end_time"]
    )

def compatible(space, request):
    """Devuelve True si el espacio cumple los requisitos obligatorios."""

    # 1. Tipo solicitado: desk o meeting_room.
    if space["type"] != request["request_type"]:
        return False

    # 2. Capacidad suficiente.
    if request["people_count"] > space["capacity"]:
        return False

    # Un puesto individual solo admite una persona.
    if space["type"] == "desk" and request["people_count"] != 1:
        return False

    # 3. Equipamiento y accesibilidad obligatorios.
    requirements = [
        ("needs_monitor", "has_monitor"),
        ("needs_videoconference", "has_videoconference"),
        ("needs_whiteboard", "has_whiteboard"),
        ("needs_accessibility", "is_accessible"),
    ]

    for requirement, attribute in requirements:
        if request[requirement] and not space[attribute]:
            return False

    # 4. Un espacio fijo solo puede reservarlo su propietario.
    if space["is_fixed"]:
        owner = space["assigned_employee_id"]

        if owner is None or owner != request["employee_id"]:
            return False

    return True

def check_assignments(spaces, requests, assignments):
    """Revisa los requisitos de cada asignación y muestra incidencias."""
    spaces_by_id = {space["id"]: space for space in spaces}

    for request in requests:
        request_id = request["id"]
        space_id = assignments.get(request_id)

        if space_id is None:
            print(f"{request_id}: NO ATENDIDA — sin espacio asignado.")
            continue

        space = spaces_by_id.get(space_id)

        if space is None:
            print(f"{request_id}: ERROR — el espacio {space_id} no existe.")
            continue

        failures = []

        if space["type"] != request["request_type"]:
            failures.append("tipo de espacio incorrecto")

        if request["people_count"] > space["capacity"]:
            failures.append("capacidad insuficiente")

        if space["type"] == "desk" and request["people_count"] != 1:
            failures.append("un puesto individual solo admite una persona")

        requirements = [
            ("needs_monitor", "has_monitor", "monitor"),
            ("needs_videoconference", "has_videoconference",
             "videoconferencia"),
            ("needs_whiteboard", "has_whiteboard", "pizarra"),
            ("needs_accessibility", "is_accessible", "accesibilidad"),
        ]

        for requirement, attribute, label in requirements:
            if request[requirement] and not space[attribute]:
                failures.append(f"falta {label}")

        if space["is_fixed"]:
            owner = space["assigned_employee_id"]
            if owner is None or owner != request["employee_id"]:
                failures.append("puesto fijo no disponible para este empleado")

        if failures:
            print(
                f"{request_id} → {space_id}: ASIGNACIÓN INVÁLIDA — "
                + "; ".join(failures)
            )
        else:
            print(
                f"{request_id} → {space_id}: "
                "todos los requisitos obligatorios se cumplen."
            )

        if request["prefers_window"] and not space["near_window"]:
            print("  Preferencia no satisfecha: ventana.")

def optimize_desks(desks, requests, now = None):
    # Validaciones básicas de los datos.
    if len({d["id"] for d in desks}) != len(desks):
        raise ValueError("Hay identificadores de puestos duplicados.")

    if len({r["id"] for r in requests}) != len(requests):
        raise ValueError("Hay identificadores de solicitudes duplicados.")

    for request in requests:
        if request["start_time"] >= request["end_time"]:
            raise ValueError(
                f"Horario incorrecto en la solicitud {request['id']}"
            )
    if now is None:
        now = datetime.now(timezone.utc)
    spaces_by_id = {space["id"]: space for space in desks}

    for request in requests:
        current_space_id = request.get("space_id")
        request_status = request.get("status", "pending")

        if request_status not in ("pending", "assigned"):
            raise ValueError(
                "El optimizador debe recibir solo reservas activas: "
                "pending o assigned."
            )

        if request_status == "assigned" and current_space_id is None:
            raise ValueError(
                f"{request['id']}: reserva asignada sin espacio."
            )

        if current_space_id is not None:
            if current_space_id not in spaces_by_id:
                raise ValueError(
                    f"{request['id']}: el espacio actual no existe."
                )

        if request["start_time"].tzinfo is None:
            raise ValueError("Las fechas deben incluir zona horaria.")
    model = cp_model.CpModel()
    x = {}

    # Crear variables solo para asignaciones compatibles.
    for i, request in enumerate(requests):
        for j, desk in enumerate(desks):
            if compatible(desk, request):
                x[i, j] = model.new_bool_var(f"x_{i}_{j}")

    # Cada solicitud recibe como máximo un puesto.
    for i in range(len(requests)):
        variables = [
            x[i, j]
            for j in range(len(desks))
            if (i, j) in x
        ]
        if variables:
            model.add(sum(variables) <= 1)

    # Conservar reservas aceptadas y bloquear su espacio a 24 h del inicio.
    for i, request in enumerate(requests):
        current_space_id = request.get("space_id")
        request_status = request.get("status", "pending")

        variables = [
            x[i, j]
            for j in range(len(desks))
            if (i, j) in x
        ]

        # Una reserva ya aceptada debe continuar atendida.
        if request_status == "assigned":
            if not variables:
                raise ValueError(
                    f"{request['id']}: no hay ningún espacio compatible "
                    "para conservar la reserva."
                )

            model.add(sum(variables) == 1)

        # El bloqueo temporal solo fija reservas que ya tienen asignación.
        # Una solicitud nueva dentro de las 12 h puede recibir su primera sala.
        locked = (
            request.get("is_locked", False)
            or (
                request_status == "assigned"
                and request["start_time"] <= now + timedelta(hours=12)
            )
        )

        if locked:
            if current_space_id is None:
                raise ValueError(
                    f"{request['id']}: reserva bloqueada sin espacio."
                )

            current_j = next(
                j for j, space in enumerate(desks)
                if space["id"] == current_space_id
            )

            if (i, current_j) not in x:
                raise ValueError(
                    f"{request['id']}: el espacio bloqueado "
                    "incumple los requisitos."
                )

            model.add(x[i, current_j] == 1)
    # Evitar conflictos de puesto y de empleado.
    for i in range(len(requests)):
        for k in range(i + 1, len(requests)):
            if not overlap(requests[i], requests[k]):
                continue

            for j in range(len(desks)):
                if (i, j) in x and (k, j) in x:
                    model.add(x[i, j] + x[k, j] <= 1)

            same_employee = (
                requests[i]["employee_id"] == requests[k]["employee_id"]
            )

            same_type = (
                requests[i]["request_type"] == requests[k]["request_type"]
            )

            if same_employee and same_type:
                variables = [
                    variable
                    for (request_index, _), variable in x.items()
                    if request_index in (i, k)
                ]
                if variables:
                    model.add(sum(variables) <= 1)

    if not x:
        return {
            "assignments": {r["id"]: None for r in requests},
            "served": 0,
            "window_preferences_met": 0,
            "changes": 0,
        }

    # Primera etapa: atender pendientes por orden de llegada.
    solver = cp_model.CpSolver()


    def arrival_key(i):
        value = requests[i].get("created_at")

        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError(
                f"{requests[i]['id']}: created_at debe incluir zona horaria."
            )

        # Desempate determinista cuando dos solicitudes tienen la misma fecha.
        return value.astimezone(timezone.utc), requests[i]["id"]


    pending_indices = [
        i for i, request in enumerate(requests)
        if request["status"] == "pending"
    ]

    pending_indices.sort(key=arrival_key)

    for i in pending_indices:
        variables = [
            x[i, j]
            for j in range(len(desks))
            if (i, j) in x
        ]

        if not variables:
            # No tiene ningún espacio compatible: sigue pendiente.
            continue

        accepted = sum(variables)
        model.maximize(accepted)

        status = solver.solve(model)

        if status != cp_model.OPTIMAL:
            raise RuntimeError(
                "No se pudo determinar la asignación FIFO: "
                f"{solver.status_name(status)}. No guardar cambios."
            )

        # Fijar si se atiende esta solicitud, sin fijar todavía su espacio.
        model.add(accepted == int(solver.value(accepted)))

    # Resolver también cuando no hay pendientes con espacios compatibles.
    model.maximize(0)
    status = solver.solve(model)

    if status != cp_model.OPTIMAL:
        raise RuntimeError(
            f"Reservas incompatibles: {solver.status_name(status)}. "
            "No guardar cambios."
        )

    served = sum(x.values())
    max_served = int(solver.value(served))

    # Las decisiones de aceptación ya están fijadas.
    # Ahora optimizamos capacidad, preferencias y cambios.
    model.add(served == max_served)
    # Plazas sobrantes en las salas asignadas.
    waste = sum(
        (desks[j]["capacity"] - requests[i]["people_count"]) * variable
        for (i, j), variable in x.items()
        if desks[j]["type"] == "meeting_room"
    )

    # Preferencias de ventana satisfechas.
    preference_terms = [
        variable
        for (i, j), variable in x.items()
        if requests[i]["prefers_window"] and desks[j]["near_window"]
    ]

    preferences_met = sum(preference_terms)

    # Penalizar cambios en reservas que ya tenían un espacio.
    change_terms = []

    for i, request in enumerate(requests):
        current_space_id = request.get("space_id")

        if request.get("status") != "assigned":
            continue

        for j, space in enumerate(desks):
            if (i, j) in x and space["id"] != current_space_id:
                change_terms.append(x[i, j])

    changes = sum(change_terms)

    # Pesos para mantener el orden estricto de prioridades:
    # capacidad sobrante > preferencias > cambios.
    max_changes = sum(
        request.get("status") == "assigned"
        for request in requests
    )

    max_preferences = sum(
        request["prefers_window"]
        for request in requests
    )

    preference_weight = max_changes + 1

    waste_weight = (
        preference_weight * max_preferences
        + max_changes
        + 1
    )

    model.minimize(
        waste_weight * waste
        - preference_weight * preferences_met
        + changes
    )

    status = solver.solve(model)

    if status != cp_model.OPTIMAL:
        raise RuntimeError(
            f"No se demostró la mejor asignación: "
            f"{solver.status_name(status)}"
        )

    assignments = {request["id"]: None for request in requests}

    for (i, j), variable in x.items():
        if solver.value(variable):
            assignments[requests[i]["id"]] = desks[j]["id"]

    return {
        "assignments": assignments,
        "served": max_served,
        "window_preferences_met": sum(
            solver.value(variable) for variable in preference_terms
        ),
        "changes": sum(
            solver.value(variable) for variable in change_terms
        ),
    }

def validate_proposal(spaces, requests, assignments, now=None):
    """Rechaza una propuesta si incumple las restricciones operativas."""
    if now is None:
        now = datetime.now(timezone.utc)

    errors = []
    spaces_by_id = {space["id"]: space for space in spaces}
    request_ids = {request["id"] for request in requests}

    if len(spaces_by_id) != len(spaces):
        errors.append("Hay espacios con ID duplicado.")

    if len(request_ids) != len(requests):
        errors.append("Hay solicitudes con ID duplicado.")

    if set(assignments) != request_ids:
        errors.append(
            "La propuesta no contiene exactamente las solicitudes evaluadas."
        )

    assigned_requests = []

    for request in requests:
        request_id = request["id"]
        start = request["start_time"]
        end = request["end_time"]

        valid_dates = (
            isinstance(start, datetime)
            and isinstance(end, datetime)
            and start.utcoffset() is not None
            and end.utcoffset() is not None
        )

        if not valid_dates:
            errors.append(f"{request_id}: fechas sin zona horaria.")
            continue

        if end <= start:
            errors.append(f"{request_id}: intervalo incorrecto.")

        people = request["people_count"]

        if type(people) is not int or people <= 0:
            errors.append(f"{request_id}: people_count debe ser positivo.")
            continue

        new_space_id = assignments.get(request_id)
        old_space_id = request.get("space_id")
        status = request["status"]

        if status not in ("pending", "assigned"):
            errors.append(f"{request_id}: estado no admitido.")

        if status == "assigned" and old_space_id is None:
            errors.append(f"{request_id}: reserva aceptada sin espacio actual.")

        if status == "assigned" and new_space_id is None:
            errors.append(f"{request_id}: una reserva aceptada pierde su espacio.")

        locked = (
            request.get("is_locked", False)
            or (
                status == "assigned"
                and start <= now + timedelta(hours=12)
            )
        )

        if locked and (
            old_space_id is None or new_space_id != old_space_id
        ):
            errors.append(f"{request_id}: se altera una reserva bloqueada.")

        if new_space_id is None:
            continue

        space = spaces_by_id.get(new_space_id)

        if space is None:
            errors.append(f"{request_id}: espacio propuesto inexistente.")
            continue

        if type(space["capacity"]) is not int or space["capacity"] <= 0:
            errors.append(f"{request_id}: capacidad del espacio incorrecta.")
            continue

        # Revisión de requisitos independiente de compatible().
        if space["type"] != request["request_type"]:
            errors.append(f"{request_id}: tipo de espacio incorrecto.")

        if people > space["capacity"]:
            errors.append(f"{request_id}: capacidad insuficiente.")

        if space["type"] == "desk" and people != 1:
            errors.append(f"{request_id}: un puesto admite una persona.")

        for requirement, attribute in [
            ("needs_monitor", "has_monitor"),
            ("needs_videoconference", "has_videoconference"),
            ("needs_whiteboard", "has_whiteboard"),
            ("needs_accessibility", "is_accessible"),
        ]:
            if request[requirement] and not space[attribute]:
                errors.append(f"{request_id}: incumple {requirement}.")

        if space["is_fixed"] and (
            space["assigned_employee_id"] is None
            or space["assigned_employee_id"] != request["employee_id"]
        ):
            errors.append(f"{request_id}: propietario del puesto incorrecto.")

        # Se aplicará cuando incorporemos el edificio a los datos.
        required_building = request.get("building_id")

        if required_building is not None:
            if space.get("building_id") != required_building:
                errors.append(f"{request_id}: edificio incorrecto.")

        assigned_requests.append(request)

    # Revisar conflictos entre reservas atendidas.
    for i, a in enumerate(assigned_requests):
        for b in assigned_requests[i + 1:]:
            simultaneous = (
                a["start_time"] < b["end_time"]
                and b["start_time"] < a["end_time"]
            )

            if not simultaneous:
                continue

            if assignments[a["id"]] == assignments[b["id"]]:
                errors.append(
                    f"{a['id']} / {b['id']}: mismo espacio simultáneamente."
                )

            if (
                a["employee_id"] == b["employee_id"]
                and a["request_type"] == b["request_type"]
            ):
                errors.append(
                    f"{a['id']} / {b['id']}: el empleado tiene "
                    "dos reservas simultáneas del mismo tipo."
                )

    if errors:
        raise ValueError(
            "Propuesta inválida. No guardar cambios:\n- "
            + "\n- ".join(errors)
        )

    return True

def demo():
    desks = [
        {
            "id": "D1",
            "has_monitor": True,
            "is_accessible": True,
            "near_window": True,
            "is_fixed": False,
            "assigned_employee_id": None,
            "type": "desk",
            "capacity": 1,
            "has_videoconference": False,
            "has_whiteboard": False,
        },
        {
            "id": "D2",
            "has_monitor": True,
            "is_accessible": False,
            "near_window": False,
            "is_fixed": False,
            "assigned_employee_id": None,
            "type": "desk",
            "capacity": 1,
            "has_videoconference": False,
            "has_whiteboard": False,
        },
        {
            "id": "D3",
            "has_monitor": False,
            "is_accessible": True,
            "near_window": True,
            "is_fixed": True,
            "assigned_employee_id": "E3",
            "type": "desk",
            "capacity": 1,
            "has_videoconference": False,
            "has_whiteboard": False,
        },
        {
            "id": "S1",
            "type": "meeting_room",
            "capacity": 4,
            "has_monitor": True,
            "has_videoconference": False,
            "has_whiteboard": True,
            "is_accessible": True,
            "near_window": True,
            "is_fixed": False,
            "assigned_employee_id": None,
        },
        {
            "id": "S2",
            "type": "meeting_room",
            "capacity": 8,
            "has_monitor": True,
            "has_videoconference": True,
            "has_whiteboard": True,
            "is_accessible": True,
            "near_window": False,
            "is_fixed": False,
            "assigned_employee_id": None,
        },
    ]

    start = datetime.fromisoformat("2026-10-08T09:00:00+02:00")
    end = datetime.fromisoformat("2026-10-08T17:00:00+02:00")

    requests = [
        {
            "id": "R1",
            "employee_id": "E1",
            "start_time": start,
            "end_time": end,
            "needs_monitor": True,
            "needs_accessibility": False,
            "prefers_window": True,
            "request_type": "desk",
            "people_count": 1,
            "needs_videoconference": False,
            "needs_whiteboard": False,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
        {
            "id": "R2",
            "employee_id": "E2",
            "start_time": start,
            "end_time": end,
            "needs_monitor": True,
            "needs_accessibility": True,
            "prefers_window": False,
            "request_type": "desk",
            "people_count": 1,
            "needs_videoconference": False,
            "needs_whiteboard": False,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
        {
            "id": "R3",
            "employee_id": "E3",
            "start_time": start,
            "end_time": end,
            "needs_monitor": False,
            "needs_accessibility": False,
            "prefers_window": True,
            "request_type": "desk",
            "people_count": 1,
            "needs_videoconference": False,
            "needs_whiteboard": False,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
        {
            "id": "R4",
            "employee_id": "E4",
            "request_type": "meeting_room",
            "people_count": 3,
            "start_time": start,
            "end_time": end,
            "needs_monitor": True,
            "needs_videoconference": False,
            "needs_whiteboard": True,
            "needs_accessibility": False,
            "prefers_window": True,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
        {
            "id": "R5",
            "employee_id": "E5",
            "request_type": "meeting_room",
            "people_count": 6,
            "start_time": start,
            "end_time": end,
            "needs_monitor": True,
            "needs_videoconference": True,
            "needs_whiteboard": False,
            "needs_accessibility": True,
            "prefers_window": False,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
        {
            "id": "R6",
            "employee_id": "E6",
            "request_type": "meeting_room",
            "people_count": 10,
            "start_time": start,
            "end_time": end,
            "needs_monitor": True,
            "needs_videoconference": False,
            "needs_whiteboard": False,
            "needs_accessibility": False,
            "prefers_window": False,
            "space_id": None,       # ID actual si ya tiene asignación
            "status": "pending",    # "assigned" si ya fue aceptada
            "is_locked": False,
        },
    ]

    for i, request in enumerate(requests):
        request["created_at"] = (
            request["start_time"]
            - timedelta(days=2)
            + timedelta(seconds=i)
        )

    result = optimize_desks(desks, requests)

    print("Solicitudes atendidas:", result["served"])
    print("Preferencias de ventana satisfechas:",
          result["window_preferences_met"])

    check_assignments(desks, requests, result["assignments"])

def test_supabase():
    from supabase_connection import load_company_data
    from booking_repository import save_proposal

    spaces, requests, snapshot, client = load_company_data()

    if not requests:
        print("No hay reservas activas para optimizar.")
        return

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

    print("Propuesta validada: no hay incumplimientos operativos.")

    saved = save_proposal(
        client,
        snapshot,
        result["assignments"],
    )

    print(
        "Propuesta guardada en Supabase. "
        f"Reservas actualizadas: {saved['updated']}"
    )

    names = {space["id"]: space["name"] for space in spaces}

    print("\nSolicitudes atendidas:", result["served"])
    print("Reservas que cambian de espacio:", result["changes"])
    print(
        "Preferencias de ventana satisfechas:",
        result["window_preferences_met"],
    )

    for request in requests:
        old_id = request.get("space_id")
        new_id = result["assignments"][request["id"]]

        old_name = names.get(old_id, "Sin asignación")
        new_name = names.get(new_id, "Sin asignación")

        print(f"{request['id']}: {old_name} → {new_name}")

    print("\nProceso terminado.")

if __name__ == "__main__":
    test_supabase()