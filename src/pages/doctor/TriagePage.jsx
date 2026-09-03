import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getTriageResults } from '../../services/doctorReportService.js'
import './DoctorPages.css'

/**
 * Doctor PENDING / Abnormal Results Triage view.
 *
 * Read-only. Lists every CandidateResult with verification_status ===
 * "pending" across every patient the doctor currently has ACTIVE
 * access to (same authorization as My Patients / the review
 * workspace). Abnormal (high/low) results are surfaced first by the
 * backend; this page never changes any candidate's status and never
 * creates a trusted result — there are no verify/correct/reject
 * controls here, only a link into the existing review workspace.
 */
function TriagePage() {
  const [results, setResults] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await getTriageResults()
        if (!cancelled) {
          setResults(data.results || [])
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Could not load triage results.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    load()

    return () => {
      cancelled = true
    }
  }, [])

  if (isLoading) {
    return (
      <div className="doctor-page">
        <h1 className="doctor-page-title">Pending Review Triage</h1>
        <div className="doctor-loading">
          <div className="doctor-spinner" />
          <p>Loading pending results...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="doctor-page">
        <h1 className="doctor-page-title">Pending Review Triage</h1>
        <div className="doctor-error">
          <p className="doctor-error-message">{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="doctor-page">
      <h1 className="doctor-page-title">Pending Review Triage</h1>

      {results.length === 0 ? (
        <div className="doctor-empty">
          <h2 className="doctor-empty-title">Nothing Pending</h2>
          <p className="doctor-empty-message">
            No AI-extracted candidate results are currently awaiting your
            review across your active patients.
          </p>
        </div>
      ) : (
        <>
          <div className="doctor-pending-notice">
            ⚠️ All results below are AI-extracted candidates, pending doctor
            review — none have been verified. Abnormal results are listed
            first.
          </div>

          <table className="doctor-results-table">
            <thead>
              <tr>
                <th>Patient</th>
                <th>Test Name</th>
                <th>Canonical Test</th>
                <th>Raw Value</th>
                <th>Normalized Value</th>
                <th>Raw Unit</th>
                <th>Normalized Unit</th>
                <th>Result Date</th>
                <th>Normalized Date</th>
                <th>Raw Reference Range</th>
                <th>Normalized Range</th>
                <th>Abnormality</th>
                <th>Evidence</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((entry) => {
                const c = entry.candidate
                const normalizedRange =
                  c.normalized_reference_lower != null || c.normalized_reference_upper != null
                    ? `${c.normalized_reference_lower ?? '—'} – ${c.normalized_reference_upper ?? '—'}`
                    : '—'
                return (
                  <tr key={c.id}>
                    <td>{entry.patient_name}</td>
                    <td>{c.test_name}</td>
                    <td>{c.canonical_test ? c.canonical_test.display_name : '—'}</td>
                    <td>{c.value}</td>
                    <td>{c.normalized_value ?? '—'}</td>
                    <td>{c.unit || '—'}</td>
                    <td>{c.normalized_unit || '—'}</td>
                    <td>{c.result_date || '—'}</td>
                    <td>{c.normalized_result_date || '—'}</td>
                    <td>{c.reference_range || '—'}</td>
                    <td>{normalizedRange}</td>
                    <td>
                      <span className={`doctor-abnormality doctor-abnormality-${c.abnormality_status}`}>
                        {c.abnormality_status}
                      </span>
                    </td>
                    <td className="doctor-triage-evidence" title={c.evidence}>
                      {c.evidence}
                    </td>
                    <td>
                      <Link
                        className="doctor-back-link"
                        to={`/doctor/patients/${entry.patient_id}/reports/${entry.report_id}/review`}
                      >
                        Open in review →
                      </Link>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

export default TriagePage
