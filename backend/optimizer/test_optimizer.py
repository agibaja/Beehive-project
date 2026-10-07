from copy import deepcopy
from datetime import datetime, timedelta, timezone

from api import optimize_desks, validate_proposal


def make_room(space_id, capacity):
    return {
        "id": space_id,
        "type": "meeting_room",
        "capacity": capacity,
        "has_monitor": True,
        "has_videoconference": True,
        "has_whiteboard": True,
        "is_accessible": True,
        "near_window": False,
        "is_fixed": False,
        "assigned_employee_id": None,
    }


def make_request(request_id, employee_id, people, start):
    return {
        "id": request_id,
        "employee_id": employee_id,
        "request_type": "meeting_room",
        "people_count": people,
        "start_time": start,
        "end_time": start + timedelta(hours=1),
        "needs_monitor": True,
        "needs_videoconference": False,
        "needs_whiteboard": False,
        "needs_accessibility": False,
        "prefers_window": False,
        "space_id": None,
        "status": "pending",
        "is_locked": False,
        "created_at": start - timedelta(days=2),
    }

def optimize_and_validate(spaces, requests, now):
    result = optimize_desks(spaces, requests, now=now)

    validate_proposal(
        spaces,
        requests,
        result["assignments"],
        now=now,
    )

    print("Propuesta validada: no hay incumplimientos operativos.")
    return result

def test_dynamic_optimization():
    now = datetime(2030, 1, 10, 9, tzinfo=timezone.utc)

    rooms = [
        make_room("SMALL", 4),
        make_room("LARGE", 10),
    ]

    def scenario(hours_until_start):
        start = now + timedelta(hours=hours_until_start)

        previous = make_request("PREVIOUS", "E1", 4, start)
        previous["space_id"] = "LARGE"
        previous["status"] = "assigned"

        new = make_request("NEW", "E2", 10, start)

        return [previous, new]

    # Caso 1: faltan 13 h. Puede trasladarse la reunión anterior.
    result = optimize_and_validate(rooms, scenario(13), now=now)

    assert result["assignments"]["PREVIOUS"] == "SMALL"
    assert result["assignments"]["NEW"] == "LARGE"
    assert result["served"] == 2
    assert result["changes"] == 1
    print("OK: reserva flexible trasladada; ambas reuniones atendidas.")

    # Casos 2 y 3: exactamente 12 h y menos de 12 h.
    for hours in (12, 10):
        result = optimize_and_validate(rooms, scenario(hours), now=now)

        assert result["assignments"]["PREVIOUS"] == "LARGE"
        assert result["assignments"]["NEW"] is None
        assert result["served"] == 1
        assert result["changes"] == 0
        print(f"OK: a {hours} h, la reserva conserva su sala.")

    # Caso 4: no existe sala alternativa, aunque falten 30 h.
    result = optimize_and_validate(
        [make_room("LARGE", 10)],
        scenario(30),
        now=now,
    )

    assert result["assignments"]["PREVIOUS"] == "LARGE"
    assert result["assignments"]["NEW"] is None
    print("OK: la nueva solicitud no expulsa a la reserva anterior.")

    # Caso 5: bloqueo manual, aunque falten más de 12 h.
    requests = deepcopy(scenario(30))
    requests[0]["is_locked"] = True

    result = optimize_and_validate(rooms, requests, now=now)

    assert result["assignments"]["PREVIOUS"] == "LARGE"
    assert result["assignments"]["NEW"] is None
    print("OK: bloqueo manual respetado.")

    # Caso 6: una solicitud de última hora puede recibir primera sala.
    new = make_request(
        "LAST_MINUTE",
        "E3",
        10,
        now + timedelta(hours=2),
    )

    result = optimize_and_validate(rooms, [new], now=now)

    assert result["assignments"]["LAST_MINUTE"] == "LARGE"
    print("OK: solicitud de última hora asignada.")

    print("\nTodas las pruebas han pasado.")


if __name__ == "__main__":
    test_dynamic_optimization()