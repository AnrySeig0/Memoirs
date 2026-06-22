"""Shared request bases for the Review UI HTTP layer."""
from pydantic import BaseModel, Field


class ActorBody(BaseModel):
    """Common shape: every state-mutating action records who did it."""

    actor: str = Field(min_length=1, description="Identifier of the reviewer.")
