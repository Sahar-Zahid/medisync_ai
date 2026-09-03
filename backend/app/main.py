"""
MediSync API — backend foundation + database connectivity + auth.

Endpoints: GET /, GET /health, GET /health/db, POST /auth/signup,
POST /auth/login, POST /auth/logout, GET /auth/me,
GET /patient/profile, PATCH /patient/profile, GET /patient/doctors,
GET /patient/doctors/{doctor_id}, POST /patient/reports,
POST /patient/reports/{report_id}/process,
POST /patient/reports/{report_id}/candidate-extraction,
GET /patient/results.
Candidate extraction produces AI-extracted, pending-verification
candidate data only — never a verified medical result. GET /patient/results
is the read-only patient view of trusted TestResult rows a doctor has
already verified or corrected — no appointment data endpoints yet.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import engine
from app.routers import (
    auth,
    doctor_reports,
    extraction,
    patient,
    relationship,
    reports,
    results,
)

app = FastAPI(title="MediSync API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(patient.router)
app.include_router(reports.router)
app.include_router(extraction.router)
app.include_router(relationship.patient_router)
app.include_router(relationship.doctor_router)
app.include_router(doctor_reports.router)
app.include_router(results.router)


@app.get("/")
def root():
    return {"message": "MediSync API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/health/db")
def health_db():
    """Verify the app can reach PostgreSQL, without ever exposing DATABASE_URL."""
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Database is not configured. Set DATABASE_URL in backend/.env.",
        )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )
    return {"status": "healthy"}
