from typing import List, Optional
from pydantic import BaseModel, Field


class Step(BaseModel):
    step: int = Field(..., description="Step order starting from 1")
    action: str = Field(..., description="Action text")
    role: Optional[str] = Field(None, description="Actor or lane if BPMN")


class AnalyzeResponse(BaseModel):
    diagram_type: str
    description: str
    steps: List[Step]


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
