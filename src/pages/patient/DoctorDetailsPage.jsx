import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { DoctorDirectoryError, fetchDoctorById } from '../../services/patientService.js'
import { DoctorsIcon } from '../../components/dashboard/icons.jsx'
import './DoctorDetailsPage.css'

function formatMemberSince(isoDate) {
  if (!isoDate) return ''
  try {
    return new Date(isoDate).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
  } catch {
    return ''
  }
}

function DoctorDetailsPage() {
  const { doctorId } = useParams()

  const [doctor, setDoctor] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let isMounted = true
    setIsLoading(true)
    setLoadError('')
    setDoctor(null)

    fetchDoctorById(doctorId)
      .then((data) => {
        if (!isMounted) return
        setDoctor(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof DoctorDirectoryError
            ? error.message
            : 'Something went wrong loading this doctor. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [doctorId])

  return (
    <div className="doctor-details-page">
      <Link to="/patient/doctors" className="doctor-details-back">
        ← Back to Doctor Directory
      </Link>

      <span className="eyebrow">Patient Dashboard</span>
      <h1>Doctor Details</h1>

      {isLoading && <p className="dashboard-card-empty">Loading doctor…</p>}

      {!isLoading && loadError && (
        <p className="field-error doctor-details-error">{loadError}</p>
      )}

      {!isLoading && !loadError && doctor && (
        <section className="dashboard-card doctor-details-card">
          <div className="doctor-details-header">
            <span className="doctor-details-icon" aria-hidden="true">
              <DoctorsIcon />
            </span>
            <div>
              <h2 className="doctor-details-name">{doctor.full_name}</h2>
              <span className="doctor-details-role">Doctor</span>
            </div>
          </div>

          <div className="doctor-field">
            <span className="doctor-field-label">On MediSync since</span>
            <span className="doctor-field-value">{formatMemberSince(doctor.created_at)}</span>
          </div>
        </section>
      )}
    </div>
  )
}

export default DoctorDetailsPage
