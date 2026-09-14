from datetime import datetime
from pydantic import BaseModel


class Source(BaseModel):
    provider: str
    url: str
    fetched_at: datetime
