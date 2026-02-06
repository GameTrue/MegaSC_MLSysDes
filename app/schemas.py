from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class Step(BaseModel):
    step: Union[int, str] = Field(..., description="Step id (int or string like 'start'/'end')")
    action: str = Field(..., description="Node text (can be empty)")
    role: Optional[str] = Field(None, description="Actor or lane if BPMN")
    type: Optional[str] = Field(None, description="Shape type (start/end/task/decision)")
    next_steps: List[Dict[str, Any]] = Field(default_factory=list, description="List of next steps with optional labels")


class AnalyzeResponse(BaseModel):
    diagram_type: str
    table: str
    raw: Optional[str] = None
    steps: Optional[List[Step]] = None


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
