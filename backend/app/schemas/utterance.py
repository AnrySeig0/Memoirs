import uuid

from pydantic import BaseModel, ConfigDict


class UtteranceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    speaker: str
    text: str
    char_start: int
    char_end: int
