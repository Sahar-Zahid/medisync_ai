"""
Pydantic schemas for Report.

Only a response shape exists here — upload is a multipart file request,
not a JSON body, so there is no ReportCreate schema.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.report import ReportStatus


class ReportResponse(BaseModel):
    """Safe representation of an uploaded report.

    Deliberately excludes storage_path and patient_id — the internal
    filesystem/storage identifier must never reach the client, and the
    owner is always implicit (the authenticated caller).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    status: ReportStatus
    created_at: datetime
