"""Demo request endpoint."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from core.database import async_session_factory

router = APIRouter()


class DemoRequest(BaseModel):
    name: str
    email: str
    company: str = ""
    role: str = ""
    phone: str = ""


@router.post("/demo-request", status_code=201)
async def submit_demo_request(body: DemoRequest):
    """Accept a demo request and persist it."""
    async with async_session_factory() as session:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS demo_requests ("
                "id SERIAL PRIMARY KEY, name TEXT, email TEXT, company TEXT, "
                "role TEXT, phone TEXT, created_at TIMESTAMPTZ DEFAULT NOW())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO demo_requests (name, email, company, role, phone) "
                "VALUES (:name, :email, :company, :role, :phone)"
            ),
            {
                "name": body.name,
                "email": body.email,
                "company": body.company,
                "role": body.role,
                "phone": body.phone,
            },
        )
        await session.commit()
    return {"status": "received", "message": "We'll be in touch within 24 hours."}
