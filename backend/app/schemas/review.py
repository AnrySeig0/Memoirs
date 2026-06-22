import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReviewLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    claim_id: uuid.UUID
    action: str
    payload: dict[str, Any] | None
    actor: str
    created_at: datetime
