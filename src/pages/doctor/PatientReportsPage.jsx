import { useEffect, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { getPatientReports, getReportPdfUrl } from '../../services/doctorReportService.js'
import './DoctorPages.css'

/**
 * Doctor's patient reports view page.
 *
 * Shows a patient's medical reports with extraction data.
 * All candidate data remains PENDING — this is view-only.
 */
function PatientReportsPage() {
  const { patientId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function loadReports() {
      try {
        const result = await getPatientReports(patientId)
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Could not load patient reports.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    loadReports()

    return () => {
      cancelled = true
    }
  }, [patientId])

  function handleViewPdf(reportId) {
    const url = getReportPdfUrl(patientId, reportId)
    window.open(url, '_blank')
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to="/doctor/patients" className="doctor-back-link">
            ← Back to My Patients
          </Link>
          <h1 className="doctor-page-title">Loading Reports...</h1>
        </div>
        <div className="doctor-loading">
          <div className="doctor-spinner" />
          <p>Loading patient reports...</p>
        </div>
      </div>
    )
  }

  // Error state
  if (error) {
    const isUnauthorized = error.includes('access')
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to="/doctor/patients" className="doctor-back-link">
            ← Back to My Patients
          </Link>
          <h1 className="doctor-page-title">
            {isUnauthorized ? 'Access Denied' : 'Error'}
          </h1>
        </div>
        <div className={`doctor-error ${isUnauthorized ? 'doctor-error-unauthorized' : ''}`}>
          <p className="doctor-error-message">{error}</p>
          {!isUnauthorized && (
            <button
              type="button"
              className="doctor-retry-button"
              onClick={() => {
                setIsLoading(true)
                setError(null)
                getPatientReports(patientId)
                  .then((result) => {
                    setData(result)
                    setIsLoading(false)
                  })
                  .catch((err) => {
                    setError(err.message || 'Could not load patient reports.')
                    setIsLoading(false)
                  })
              }}
            >
              Try Again
            </button>
          )}
        </div>
      </div>
    )
  }

  // Empty state
  if (!data || data.reports.length === 0) {
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to="/doctor/patients" className="doctor-back-link">
            ← Back to My Patients
          </Link>
          <h1 className="doctor-page-title">{data?.patient_name || 'Patient'}'s Reports</h1>
        </div>
        <div className="doctor-empty">
          <div className="doctor-empty-icon" aria-hidden="true">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M7 3.5h7l4 4V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
              <path d="M14 3.5V8h4" />
            </svg>
          </div>
          <h2 className="doctor-empty-title">No Reports Yet</h2>
          <p className="doctor-empty-message">
            This patient hasn't uploaded any medical reports yet.
          </p>
        </div>
      </div>
    )
  }

  // Reports list
  return (
    <div className="doctor-page">
      <div className="doctor-page-header">
        <Link to="/doctor/patients" className="doctor-back-link">
          ← Back to My Patients
        </Link>
        <h1 className="doctor-page-title">{data.patient_name}'s Reports</h1>
      </div>

      <div className="doctor-reports-list">
        {data.reports.map((report) => (
          <div key={report.id} className="doctor-report-card">
            <div className="doctor-report-header">
              <div className="doctor-report-info">
                <h3 className="doctor-report-filename">{report.original_filename}</h3>
                <span className={`doctor-report-status doctor-report-status-${report.status}`}>
                  {report.status.charAt(0).toUpperCase() + report.status.slice(1)}
                </span>
              </div>
              <div className="doctor-report-actions">
                <button
                  type="button"
                  className="doctor-review-button"
                  onClick={() => navigate(`/doctor/patients/${patientId}/reports/${report.id}/review`)}
                >
                  Review Report
                </button>
                <button
                  type="button"
                  className="doctor-view-pdf-button"
                  onClick={() => handleViewPdf(report.id)}
                >
                  View Original PDF
                </button>
              </div>
            </div>

            <div className="doctor-report-meta">
              <span>Uploaded: {new Date(report.created_at).toLocaleDateString()}</span>
            </div>

            {/* Extraction Data */}
            {report.extraction && (
              <div className="doctor-extraction-section">
                <div className="doctor-extraction-header">
                  <h4>Extracted Lab Results</h4>
                  <span className={`doctor-extraction-status doctor-extraction-status-${report.extraction.status}`}>
                    {report.extraction.status === 'completed' ? 'Extraction Complete' : 'Extraction Failed'}
                  </span>
                </div>

                {report.extraction.status === 'completed' && report.extraction.results.length > 0 && (
                  <div className="doctor-candidate-results">
                    <div className="doctor-pending-notice">
                      ⚠️ Results are pending doctor review — not yet verified
                    </div>
                    <table className="doctor-results-table">
                      <thead>
                        <tr>
                          <th>Test Name</th>
                          <th>Value</th>
                          <th>Unit</th>
                          <th>Reference Range</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.extraction.results.map((result) => (
                          <tr key={result.id}>
                            <td>{result.test_name}</td>
                            <td>{result.value}</td>
                            <td>{result.unit || '—'}</td>
                            <td>{result.reference_range || '—'}</td>
                            <td>
                              <span className={`doctor-abnormality doctor-abnormality-${result.abnormality_status}`}>
                                {result.abnormality_status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {report.extraction.status === 'completed' && report.extraction.results.length === 0 && (
                  <p className="doctor-no-results">No lab values extracted from this report.</p>
                )}

                {report.extraction.status === 'failed' && (
                  <p className="doctor-extraction-error">
                    {report.extraction.error_message || 'Extraction failed.'}
                  </p>
                )}
              </div>
            )}

            {!report.extraction && report.status === 'completed' && (
              <div className="doctor-extraction-section">
                <p className="doctor-no-extraction">No extraction data available.</p>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default PatientReportsPage
