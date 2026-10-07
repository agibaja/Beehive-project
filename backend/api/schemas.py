from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class BookingCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    request_type: Literal["desk", "meeting_room"]
    start_time: AwareDatetime
    end_time: AwareDatetime
    request_id: UUID | None = None
    people_count: int = Field(ge=1, strict=True)

    needs_monitor: bool = False
    needs_videoconference: bool = False
    needs_whiteboard: bool = False
    needs_accessibility: bool = False
    prefers_window: bool = False

    @model_validator(mode="after")
    def validate_booking(self):
        now = datetime.now(timezone.utc)

        if self.end_time <= self.start_time:
            raise ValueError(
                "La hora final debe ser posterior a la inicial."
            )

        if self.start_time <= now:
            raise ValueError(
                "La reserva debe comenzar en el futuro."
            )

        if self.request_type == "desk" and self.people_count != 1:
            raise ValueError(
                "Un puesto de trabajo debe solicitarse para una persona."
            )

        return self