from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class FileItem(BaseModel):
    path: str
    filename: str
    relpath: str | None = None
    ext: str | None = None
    size_bytes: int | None = None
    preview_text: str | None = None


class RenameAction(BaseModel):
    type: Literal["rename"] = "rename"
    src: str
    dst: str
    reason: str | None = None


class CreateDirAction(BaseModel):
    type: Literal["mkdir"] = "mkdir"
    dir_path: str
    reason: str | None = None


class ExecutionPlan(BaseModel):
    plan_id: str | None = Field(default_factory=lambda: str(uuid4()))
    execution_id: str | None = Field(default_factory=lambda: str(uuid4()))
    created_at: str | None = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    summary: str
    create_dirs: list[CreateDirAction] = Field(default_factory=list)
    rename_files: list[RenameAction] = Field(default_factory=list)

    # Seguridad: operaciones permitidas explícitas
    allowed_operations: list[str] = Field(default_factory=lambda: ["mkdir", "rename"])


class FileAnalysis(BaseModel):
    category: str
    suggested_name: str
    reason: str



class PlannerInput(BaseModel):
    root_dir: str
    files: list[FileItem]


class PlannerOutput(BaseModel):
    plan: ExecutionPlan

