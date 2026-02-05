from typing import List, Optional
from pydantic import BaseModel, Field


class Step(BaseModel):
    step: int = Field(..., description="Step order starting from 1")
    action: str = Field(..., description="Action text")
    role: Optional[str] = Field(None, description="Actor or lane if BPMN")
    next_steps: list[dict] = Field(default_factory=list, description="List of next steps with optional condition")


class Edge(BaseModel):
    from_id: int = Field(..., description="Source step id")
    to_id: int = Field(..., description="Target step id")
    label: Optional[str] = Field(None, description="Condition/arrow label (да/нет etc.)")


class AnalyzeResponse(BaseModel):
    diagram_type: str
    description: str
    steps: List[Step]
    edges: List[Edge] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
