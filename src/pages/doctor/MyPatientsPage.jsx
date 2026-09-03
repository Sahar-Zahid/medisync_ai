import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getMyPatients } from '../../services/doctorService.js'
import './DoctorPages.css'

/**
 * Doctor's My Patients roster page.
 *
 * Shows ONLY patients connected through ACTIVE DoctorPatientLinks.
 * Loading, empty, and error states are all handled.
 */
function MyPatientsPage() {
  const [patients, setPatients] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false

    async function loadRoster() {
      try {
        const data = await getMyPatients()
        if (!cancelled) {
          setPatients(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Could not load patients.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    loadRoster()

    return () => {
      cancelled = true
    }
  }, [])

  function handleSelectPatient(patientId) {
    navigate(`/doctor/patients/${patientId}`)
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="doctor-page">
        <h1 className="doctor-page-title">My Patients</h1>
        <div className="doctor-loading">
          <div className="doctor-spinner" />
          <p>Loading your patients...</p>
        </div>
      </div>
    )
  }

  // Error state
  if (error) {
    return (
      <div className="doctor-page">
        <h1 className="doctor-page-title">My Patients</h1>
        <div className="doctor-error">
          <p className="doctor-error-message">{error}</p>
          <button
            type="button"
            className="doctor-retry-button"
            onClick={() => {
              setIsLoading(true)
              setError(null)
              getMyPatients()
                .then((data) => {
                  setPatients(data)
                  setIsLoading(false)
                })
                .catch((err) => {
                  setError(err.message || 'Could not load patients.')
                  setIsLoading(false)
                })
            }}
          >
            Try Again
          </button>
        </div>
      </div>
    )
  }

  // Empty state
  if (patients.length === 0) {
    return (
      <div className="doctor-page">
        <h1 className="doctor-page-title">My Patients</h1>
        <div className="doctor-empty">
          <div className="doctor-empty-icon" aria-hidden="true">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="9" cy="8" r="3" />
              <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
            </svg>
          </div>
          <h2 className="doctor-empty-title">No Active Patients Yet</h2>
          <p className="doctor-empty-message">
            You don't have any active patient relationships yet.
            When a patient relationship becomes active, it will appear here.
          </p>
        </div>
      </div>
    )
  }

  // Patient roster
  return (
    <div className="doctor-page">
      <h1 className="doctor-page-title">My Patients</h1>
      <div className="doctor-roster">
        {patients.map((patient) => (
          <div key={patient.patient_id} className="doctor-patient-card">
            <div className="doctor-patient-info">
              <div className="doctor-patient-avatar" aria-hidden="true">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="12" cy="8" r="3.5" />
                  <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
                </svg>
              </div>
              <div className="doctor-patient-details">
                <h3 className="doctor-patient-name">{patient.patient_name}</h3>
                <span className="doctor-patient-status">
                  Active relationship
                </span>
              </div>
            </div>
            <button
              type="button"
              className="doctor-patient-action"
              onClick={() => handleSelectPatient(patient.patient_id)}
            >
              Open Patient
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

export default MyPatientsPage
