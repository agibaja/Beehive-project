def save_proposal(client, snapshot, assignments):
    """Guardar toda la propuesta mediante una transacción en Supabase."""

    expected_ids = {
        row["id"]
        for row in snapshot["bookings"]
    }

    if set(assignments) != expected_ids:
        raise ValueError(
            "Las asignaciones no corresponden a las reservas leídas."
        )

    response = (
        client.rpc(
            "save_optimizer_proposal",
            {
                "p_snapshot": snapshot,
                "p_assignments": assignments,
            },
        )
        .execute()
    )

    result = response.data

    if not isinstance(result, dict) or result.get("saved") is not True:
        raise RuntimeError(
            "Supabase no confirmó el guardado de la propuesta."
        )

    return result