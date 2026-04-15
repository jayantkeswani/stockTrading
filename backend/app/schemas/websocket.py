from datetime import datetime

from pydantic import BaseModel


class WSMessage(BaseModel):
    event: str
    data: dict
    timestamp: datetime
