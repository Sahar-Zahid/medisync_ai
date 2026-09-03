import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { DoctorDirectoryError, fetchDoctors } from '../../services/patientService.js'
import { DoctorsIcon } from '../../components/dashboard/icons.jsx'
import './DoctorsPage.css'

function DoctorsPage() {
  const [doctors, setDoctors] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [searchTerm, setSearchTerm] = useState('')

  useEffect(() => {
    let isMounted = true

    fetchDoctors()
      .then((data) => {
        if (!isMounted) return
        setDoctors(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof DoctorDirectoryError
            ? error.message
            : 'Something went wrong loading the doctor directory. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [])

  const filteredDoctors = useMemo(() => {
    const query = searchTerm.trim().toLowerCase()
    if (!query) return doctors
    return doctors.filter((doctor) => doctor.full_name.toLowerCase().includes(query))
  }, [doctors, searchTerm])

  return (
    <div className="doctors-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>Find a Doctor</h1>
      <p className="section-intro">
        Browse the doctors registered with MediSync and keep track of who you're working with.
      </p>

      {!isLoading && !loadError && doctors.length > 0 && (
        <div className="doctors-search">
          <input
            type="search"
            className="form-input"
            placeholder="Search doctors by name…"
            aria-label="Search doctors by name"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
          />
        </div>
      )}

      {isLoading && <p className="dashboard-card-empty">Loading doctors…</p>}

      {!isLoading && loadError && <p className="field-error doctors-load-error">{loadError}</p>}

      {!isLoading && !loadError && doctors.length === 0 && (
        <p className="dashboard-card-empty doctors-empty-state">
          No doctors are registered with MediSync yet.
        </p>
      )}

      {!isLoading && !loadError && doctors.length > 0 && filteredDoctors.length === 0 && (
        <p className="dashboard-card-empty doctors-empty-state">
          No doctors match "{searchTerm}".
        </p>
      )}

      {!isLoading && !loadError && filteredDoctors.length > 0 && (
        <div className="doctors-grid">
          {filteredDoctors.map((doctor) => (
            <Link
              key={doctor.id}
              to={`/patient/doctors/${doctor.id}`}
              className="dashboard-card doctor-card"
            >
              <span className="doctor-card-icon" aria-hidden="true">
                <DoctorsIcon />
              </span>
              <div className="doctor-card-body">
                <h2 className="doctor-card-name">{doctor.full_name}</h2>
                <span className="doctor-card-role">Doctor</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

export default DoctorsPage
