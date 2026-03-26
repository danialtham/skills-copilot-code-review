"""
Announcement endpoints for the High School Management System API.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from ..database import announcements_collection, teachers_collection

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/announcements",
    tags=["announcements"]
)


class AnnouncementPayload(BaseModel):
    message: str
    start_date: Optional[str] = None
    expiration_date: str


def _require_authenticated_user(teacher_username: Optional[str]) -> Dict[str, Any]:
    if not teacher_username:
        raise HTTPException(status_code=401, detail="Authentication required")

    teacher = teachers_collection.find_one({"_id": teacher_username})
    if not teacher:
        raise HTTPException(status_code=401, detail="Invalid teacher credentials")

    return teacher


def _parse_iso_date(value: Optional[str], field_name: str, required: bool = False) -> Optional[date]:
    if value in (None, ""):
        if required:
            raise HTTPException(status_code=400, detail=f"{field_name} is required")
        return None

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must use YYYY-MM-DD format"
        ) from exc


def _serialize_announcement(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": document["_id"],
        "message": document["message"],
        "start_date": document.get("start_date"),
        "expiration_date": document["expiration_date"],
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "created_by": document.get("created_by"),
        "updated_by": document.get("updated_by")
    }


def _build_announcement_document(
    payload: AnnouncementPayload,
    teacher_username: str,
    existing_document: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    if len(message) > 280:
        raise HTTPException(status_code=400, detail="message must be 280 characters or less")

    start_date = _parse_iso_date(payload.start_date, "start_date")
    expiration_date = _parse_iso_date(payload.expiration_date, "expiration_date", required=True)

    if start_date and start_date > expiration_date:
        raise HTTPException(
            status_code=400,
            detail="start_date cannot be later than expiration_date"
        )

    timestamp = datetime.now(timezone.utc).isoformat()
    document = {
        "message": message,
        "start_date": start_date.isoformat() if start_date else None,
        "expiration_date": expiration_date.isoformat(),
        "updated_at": timestamp,
        "updated_by": teacher_username
    }

    if existing_document:
        document["created_at"] = existing_document.get("created_at", timestamp)
        document["created_by"] = existing_document.get("created_by", teacher_username)
    else:
        document["created_at"] = timestamp
        document["created_by"] = teacher_username

    return document


@router.get("", response_model=List[Dict[str, Any]])
@router.get("/", response_model=List[Dict[str, Any]])
def list_active_announcements() -> List[Dict[str, Any]]:
    """Return currently active announcements for the public site."""
    today = date.today().isoformat()
    query = {
        "expiration_date": {"$gte": today},
        "$or": [
            {"start_date": None},
            {"start_date": {"$exists": False}},
            {"start_date": {"$lte": today}}
        ]
    }

    try:
        announcements = announcements_collection.find(query).sort([
            ("expiration_date", 1),
            ("start_date", 1),
            ("created_at", -1)
        ])
        return [_serialize_announcement(document) for document in announcements]
    except PyMongoError:
        logger.exception("Unable to load active announcements")
        raise HTTPException(status_code=500, detail="Unable to load announcements")


@router.get("/manage", response_model=List[Dict[str, Any]])
def list_all_announcements(teacher_username: str = Query(...)) -> List[Dict[str, Any]]:
    """Return all announcements for authenticated management."""
    _require_authenticated_user(teacher_username)

    try:
        announcements = announcements_collection.find().sort([
            ("expiration_date", 1),
            ("start_date", 1),
            ("created_at", -1)
        ])
        return [_serialize_announcement(document) for document in announcements]
    except PyMongoError:
        logger.exception("Unable to load managed announcements")
        raise HTTPException(status_code=500, detail="Unable to load announcements")


@router.post("", response_model=Dict[str, Any])
@router.post("/", response_model=Dict[str, Any])
def create_announcement(
    payload: AnnouncementPayload,
    teacher_username: str = Query(...)
) -> Dict[str, Any]:
    """Create a new announcement for the site."""
    teacher = _require_authenticated_user(teacher_username)
    document = _build_announcement_document(payload, teacher["username"])
    document["_id"] = uuid4().hex

    try:
        announcements_collection.insert_one(document)
        return {
            "message": "Announcement published.",
            "announcement": _serialize_announcement(document)
        }
    except PyMongoError:
        logger.exception("Unable to create announcement")
        raise HTTPException(status_code=500, detail="Unable to save announcement")


@router.put("/{announcement_id}", response_model=Dict[str, Any])
def update_announcement(
    announcement_id: str,
    payload: AnnouncementPayload,
    teacher_username: str = Query(...)
) -> Dict[str, Any]:
    """Update an existing announcement."""
    teacher = _require_authenticated_user(teacher_username)
    existing_document = announcements_collection.find_one({"_id": announcement_id})
    if not existing_document:
        raise HTTPException(status_code=404, detail="Announcement not found")

    document = _build_announcement_document(
        payload,
        teacher["username"],
        existing_document=existing_document
    )

    try:
        announcements_collection.update_one(
            {"_id": announcement_id},
            {"$set": document}
        )
        return {
            "message": "Announcement updated.",
            "announcement": _serialize_announcement({"_id": announcement_id, **document})
        }
    except PyMongoError:
        logger.exception("Unable to update announcement %s", announcement_id)
        raise HTTPException(status_code=500, detail="Unable to save announcement")


@router.delete("/{announcement_id}", response_model=Dict[str, str])
def delete_announcement(
    announcement_id: str,
    teacher_username: str = Query(...)
) -> Dict[str, str]:
    """Delete an existing announcement."""
    _require_authenticated_user(teacher_username)

    try:
        result = announcements_collection.delete_one({"_id": announcement_id})
    except PyMongoError:
        logger.exception("Unable to delete announcement %s", announcement_id)
        raise HTTPException(status_code=500, detail="Unable to delete announcement")

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    return {"message": "Announcement deleted."}