import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getPatientReports, getReportPdfUrl, verifyCandidate, correctCandidate, rejectCandidate, confirmIdentity, getVerificationHistory } from '../../services/doctorReportService.js'
import './DoctorPages.css'
import './DoctorReview.css'

/**
 * Doctor Review Workspace page.
 *
 * Split-view layout showing:
 * - Left: Original medical PDF
 * - Right: Candidate-by-candidate review panel
 *
 * This is a READ-ONLY review workspace.
 * All candidate data remains PENDING — no verification decisions are made here.
 */
function DoctorReviewPage() {
  const { patientId, reportId } = useParams()
  const [data, setData] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [verifyLoading, setVerifyLoading] = useState(null)
  const [verifySuccess, setVerifySuccess] = useState(null)
  const [verifyError, setVerifyError] = useState(null)
  const [showCorrectModal, setShowCorrectModal] = useState(false)
  const [correctingCandidate, setCorrectingCandidate] = useState(null)
  const [correctForm, setCorrectForm] = useState({
    test_name: '', value: '', unit: '', reference_range: '', result_date: '', reason: '',
  })
  const [correctLoading, setCorrectLoading] = useState(false)
  const [correctError, setCorrectError] = useState('')
  const [correctSuccess, setCorrectSuccess] = useState(false)
  const [showRejectModal, setShowRejectModal] = useState(false)
  const [rejectingCandidate, setRejectingCandidate] = useState(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectLoading, setRejectLoading] = useState(false)
  const [rejectError, setRejectError] = useState('')
  const [confirmIdentityLoading, setConfirmIdentityLoading] = useState(false)
  const [confirmIdentityError, setConfirmIdentityError] = useState('')
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState('')

  const loadData = useCallback(async () => {
    try {
      const result = await getPatientReports(patientId)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err.message || 'Could not load report for review.')
    }

    // Audit history is read-only and non-fatal: if it fails, the review
    // workspace still works and the section shows an error state.
    if (reportId) {
      try {
        const historyResult = await getVerificationHistory(patientId, reportId)
        setHistory(historyResult.history || [])
        setHistoryError('')
      } catch (err) {
        setHistoryError(err.message || 'Could not load verification history.')
      }
    } else {
      setHistory([])
    }
    setHistoryLoading(false)
  }, [patientId, reportId])

  useEffect(() => {
    let cancelled = false

    async function loadReviewData() {
      await loadData()
      if (!cancelled) {
        setIsLoading(false)
      }
    }

    loadReviewData()

    return () => {
      cancelled = true
    }
  }, [loadData])

  function openCorrectModal(candidate) {
    setCorrectingCandidate(candidate)
    setCorrectForm({
      test_name: '', value: '', unit: '', reference_range: '', result_date: '', reason: '',
    })
    setCorrectError('')
    setCorrectSuccess(false)
    setShowCorrectModal(true)
  }

  function closeCorrectModal() {
    setShowCorrectModal(false)
    setCorrectingCandidate(null)
    setCorrectError('')
    setCorrectSuccess(false)
  }

  async function handleCorrect() {
    if (!correctForm.reason.trim()) {
      setCorrectError('A correction reason is required.')
      return
    }

    setCorrectLoading(true)
    setCorrectError('')

    try {
      // Build correction payload — only include fields that were changed
      const payload = { reason: correctForm.reason.trim() }
      if (correctForm.test_name.trim()) payload.test_name = correctForm.test_name.trim()
      if (correctForm.value.trim()) payload.value = correctForm.value.trim()
      if (correctForm.unit.trim()) payload.unit = correctForm.unit.trim()
      if (correctForm.reference_range.trim()) payload.reference_range = correctForm.reference_range.trim()
      if (correctForm.result_date.trim()) payload.result_date = correctForm.result_date.trim()

      await correctCandidate(patientId, reportId, correctingCandidate.id, payload)
      setCorrectSuccess(true)
      closeCorrectModal()
      await loadData()
    } catch (err) {
      setCorrectError(err.message || 'Could not correct candidate.')
    } finally {
      setCorrectLoading(false)
    }
  }

  function openRejectModal(candidate) {
    setRejectingCandidate(candidate)
    setRejectReason('')
    setRejectError('')
    setShowRejectModal(true)
  }

  function closeRejectModal() {
    setShowRejectModal(false)
    setRejectingCandidate(null)
    setRejectReason('')
    setRejectError('')
  }

  async function handleReject() {
    if (!rejectReason.trim()) {
      setRejectError('A rejection reason is required.')
      return
    }

    setRejectLoading(true)
    setRejectError('')

    try {
      await rejectCandidate(patientId, reportId, rejectingCandidate.id, rejectReason.trim())
      closeRejectModal()
      await loadData()
    } catch (err) {
      setRejectError(err.message || 'Could not reject candidate.')
    } finally {
      setRejectLoading(false)
    }
  }

  async function handleConfirmIdentity() {
    setConfirmIdentityLoading(true)
    setConfirmIdentityError('')

    try {
      await confirmIdentity(patientId, reportId)
      await loadData()
    } catch (err) {
      setConfirmIdentityError(err.message || 'Could not confirm identity checkpoint.')
    } finally {
      setConfirmIdentityLoading(false)
    }
  }

  async function handleVerify(candidateId) {
    setVerifyLoading(candidateId)
    setVerifyError(null)
    setVerifySuccess(null)

    try {
      await verifyCandidate(patientId, reportId, candidateId)
      setVerifySuccess(candidateId)
      // Refresh data to show updated verification status
      await loadData()
    } catch (err) {
      setVerifyError(err.message || 'Could not verify candidate.')
    } finally {
      setVerifyLoading(null)
    }
  }

  // Find the specific report from the data
  const report = data?.reports?.find(r => r.id === reportId)
  const candidates = report?.extraction?.results || []
  const selectedCandidate = candidates[selectedIndex] || null

  // Loading state
  if (isLoading) {
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to={`/doctor/patients/${patientId}`} className="doctor-back-link">
            ← Back to Reports
          </Link>
          <h1 className="doctor-page-title">Loading Review Workspace...</h1>
        </div>
        <div className="doctor-loading">
          <div className="doctor-spinner" />
          <p>Loading report data for review...</p>
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
          <Link to={`/doctor/patients/${patientId}`} className="doctor-back-link">
            ← Back to Reports
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
                    setError(err.message || 'Could not load report for review.')
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

  // Report not found
  if (!report) {
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to={`/doctor/patients/${patientId}`} className="doctor-back-link">
            ← Back to Reports
          </Link>
          <h1 className="doctor-page-title">Report Not Found</h1>
        </div>
        <div className="doctor-empty">
          <h2 className="doctor-empty-title">Report Not Available</h2>
          <p className="doctor-empty-message">
            The requested report could not be found or you don't have access to it.
          </p>
        </div>
      </div>
    )
  }

  // Empty candidates state
  if (candidates.length === 0) {
    return (
      <div className="doctor-page">
        <div className="doctor-page-header">
          <Link to={`/doctor/patients/${patientId}`} className="doctor-back-link">
            ← Back to Reports
          </Link>
          <h1 className="doctor-page-title">Review: {report.original_filename}</h1>
        </div>
        <div className="review-empty">
          <div className="review-pdf-panel">
            <div className="review-pdf-header">
              <h3>Original Medical PDF</h3>
            </div>
            <iframe
              src={getReportPdfUrl(patientId, reportId)}
              className="review-pdf-viewer"
              title="Original Medical Report PDF"
            />
          </div>
          <div className="review-candidate-panel">
            <div className="review-candidate-header">
              <h3>Candidate Review</h3>
              <span className="review-pending-badge">PENDING REVIEW</span>
            </div>
            <div className="review-empty-candidates">
              <p>No extracted candidates available for this report.</p>
              <p className="review-empty-hint">
                The extraction may not have completed or no lab values were found.
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Main review workspace
  return (
    <div className="doctor-page review-page">
      <div className="doctor-page-header">
        <Link to={`/doctor/patients/${patientId}`} className="doctor-back-link">
          ← Back to Reports
        </Link>
        <h1 className="doctor-page-title">Review: {report.original_filename}</h1>
      </div>

      <div className="review-workspace">
        {/* Left Panel: PDF Viewer */}
        <div className="review-pdf-panel">
          <div className="review-pdf-header">
            <h3>Original Medical PDF</h3>
          </div>
          <iframe
            src={getReportPdfUrl(patientId, reportId)}
            className="review-pdf-viewer"
            title="Original Medical Report PDF"
          />
        </div>

        {/* Right Panel: Candidate Review */}
        <div className="review-candidate-panel">
          <div className="review-candidate-header">
            <h3>Candidate Review</h3>
            <span className="review-pending-badge">🟡 PENDING REVIEW</span>
          </div>

          {/* Identity Checkpoint */}
          <div className="review-identity-section">
            <div className="review-identity-header">
              <h4>Identity Checkpoint</h4>
              <span className={`review-identity-status review-identity-status-${report.identity_check_status || 'not_checked'}`}>
                {report.identity_check_status === 'match' && '✓ MATCH'}
                {report.identity_check_status === 'mismatch' && '⚠️ MISMATCH'}
                {report.identity_check_status === 'unresolved' && '⚠️ UNRESOLVED'}
                {report.identity_check_status === 'not_checked' && '— NOT CHECKED'}
                {!report.identity_check_status && '— NOT CHECKED'}
              </span>
            </div>

            {/* Extracted identity info */}
            {report.identity_check_status !== 'not_checked' && (
              <div className="review-identity-details">
                {report.patient_name_extracted && (
                  <div className="review-identity-item">
                    <span className="review-identity-label">Extracted Name</span>
                    <span className="review-identity-value">{report.patient_name_extracted}</span>
                  </div>
                )}
                {report.patient_dob_extracted && (
                  <div className="review-identity-item">
                    <span className="review-identity-label">Extracted DOB</span>
                    <span className="review-identity-value">{report.patient_dob_extracted}</span>
                  </div>
                )}
                {report.patient_mrn_extracted && (
                  <div className="review-identity-item">
                    <span className="review-identity-label">Extracted MRN</span>
                    <span className="review-identity-value">{report.patient_mrn_extracted}</span>
                  </div>
                )}
              </div>
            )}

            {/* Mismatch/Unresolved warning + confirm button */}
            {(report.identity_check_status === 'mismatch' || report.identity_check_status === 'unresolved') && !report.identity_confirmed_by_doctor && (
              <div className="review-identity-warning">
                <p className="review-identity-warning-text">
                  {report.identity_check_status === 'mismatch'
                    ? 'The extracted patient name does not match the account. Verify/correct cannot proceed until a doctor confirms.'
                    : 'Insufficient identity evidence in the report. Verify/correct cannot proceed until a doctor confirms.'}
                </p>
                {confirmIdentityError && (
                  <p className="review-identity-error">{confirmIdentityError}</p>
                )}
                <button
                  type="button"
                  className="review-identity-confirm-button"
                  onClick={handleConfirmIdentity}
                  disabled={confirmIdentityLoading}
                >
                  {confirmIdentityLoading ? 'Confirming...' : 'Confirm Identity Checkpoint'}
                </button>
              </div>
            )}

            {/* Confirmed badge */}
            {report.identity_confirmed_by_doctor && (
              <div className="review-identity-confirmed">
                <span className="review-identity-confirmed-badge">✓ CONFIRMED BY DOCTOR</span>
                {report.identity_confirmed_at && (
                  <span className="review-identity-confirmed-time">
                    {new Date(report.identity_confirmed_at).toLocaleString()}
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Verification History (read-only audit) */}
          <div className="review-history-section">
            <div className="review-history-header">
              <h4>Verification History</h4>
              {!historyLoading && !historyError && history.length > 0 && (
                <span className="review-history-count">
                  {history.length} {history.length === 1 ? 'action' : 'actions'}
                </span>
              )}
            </div>

            {historyLoading && (
              <div className="review-history-state">
                <p>Loading audit history...</p>
              </div>
            )}

            {!historyLoading && historyError && (
              <div className="review-history-error">
                <p>{historyError}</p>
              </div>
            )}

            {!historyLoading && !historyError && history.length === 0 && (
              <div className="review-history-state">
                <p>No doctor actions recorded for this report yet.</p>
              </div>
            )}

            {!historyLoading && !historyError && history.length > 0 && (
              <div className="review-history-list">
                {history.map((entry) => (
                  <div
                    key={entry.id}
                    className={`review-history-item review-history-item-${entry.action}`}
                  >
                    <div className="review-history-item-header">
                      <span className={`review-history-badge review-history-badge-${entry.action}`}>
                        {entry.action === 'verify' && '✓ VERIFY'}
                        {entry.action === 'correct' && '✎ CORRECT'}
                        {entry.action === 'reject' && '✗ REJECT'}
                      </span>
                      <span className="review-history-time">
                        {new Date(entry.created_at).toLocaleString()}
                      </span>
                    </div>

                    <div className="review-history-meta">
                      <span className="review-history-meta-item">
                        Candidate: <strong>{entry.old_test_name || '—'}</strong>
                      </span>
                      <span className="review-history-meta-item">
                        Doctor: <strong>{entry.doctor_id ? entry.doctor_id.slice(0, 8) : '—'}</strong>
                      </span>
                    </div>

                    {entry.action === 'correct' && (
                      <div className="review-history-changes">
                        <span className="review-history-changes-label">Changed:</span>
                        {entry.new_value != null && entry.new_value !== entry.old_value && (
                          <span className="review-history-change">value {entry.old_value ?? '—'} → {entry.new_value}</span>
                        )}
                        {entry.new_unit != null && entry.new_unit !== entry.old_unit && (
                          <span className="review-history-change">unit {entry.old_unit ?? '—'} → {entry.new_unit}</span>
                        )}
                        {entry.new_test_name != null && entry.new_test_name !== entry.old_test_name && (
                          <span className="review-history-change">test {entry.old_test_name ?? '—'} → {entry.new_test_name}</span>
                        )}
                        {entry.new_reference_range != null && entry.new_reference_range !== entry.old_reference_range && (
                          <span className="review-history-change">range {entry.old_reference_range ?? '—'} → {entry.new_reference_range}</span>
                        )}
                        {entry.new_result_date != null && entry.new_result_date !== entry.old_result_date && (
                          <span className="review-history-change">date {entry.old_result_date ?? '—'} → {entry.new_result_date}</span>
                        )}
                        {entry.new_abnormality_status != null && entry.new_abnormality_status !== entry.old_abnormality_status && (
                          <span className="review-history-change">abnormality {entry.old_abnormality_status ?? '—'} → {entry.new_abnormality_status}</span>
                        )}
                        {entry.new_value == null && entry.new_unit == null && entry.new_test_name == null && entry.new_reference_range == null && entry.new_result_date == null && entry.new_abnormality_status == null && (
                          <span className="review-history-change">no value changes recorded</span>
                        )}
                      </div>
                    )}

                    {entry.action === 'verify' && entry.new_value != null && (
                      <div className="review-history-changes">
                        <span className="review-history-changes-label">Accepted as-is:</span>
                        <span className="review-history-change">{entry.new_value} {entry.new_unit || ''}</span>
                      </div>
                    )}

                    {entry.reason && (
                      <div className="review-history-reason">
                        <span className="review-history-reason-label">
                          {entry.action === 'reject' ? 'Rejection reason:' : 'Reason:'}
                        </span>
                        <p className="review-history-reason-text">{entry.reason}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Candidate List */}
          <div className="review-candidate-list">
            <h4 className="review-list-title">
              Candidates ({candidates.length})
            </h4>
            <div className="review-list-items">
              {candidates.map((candidate, index) => (
                <button
                  key={candidate.id}
                  type="button"
                  className={`review-list-item ${index === selectedIndex ? 'review-list-item-active' : ''}`}
                  onClick={() => setSelectedIndex(index)}
                >
                  <span className="review-list-index">{index + 1}</span>
                  <span className="review-list-name">{candidate.test_name}</span>
                  <span className={`review-list-status review-list-status-${candidate.verification_status}`}>
                    {candidate.verification_status === 'verified' ? '✓' : candidate.verification_status === 'corrected' ? '✎' : candidate.verification_status === 'rejected' ? '✗' : '•'}
                  </span>
                  <span className={`review-list-abnormality review-list-abnormality-${candidate.abnormality_status}`}>
                    {candidate.abnormality_status}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Selected Candidate Detail */}
          {selectedCandidate && (
            <div className="review-candidate-detail">
              <div className="review-detail-header">
                <h4>{selectedCandidate.test_name}</h4>
                {selectedCandidate.verification_status === 'verified' ? (
                  <span className="review-verified-badge">✓ VERIFIED</span>
                ) : selectedCandidate.verification_status === 'corrected' ? (
                  <span className="review-corrected-badge">✎ CORRECTED</span>
                ) : selectedCandidate.verification_status === 'rejected' ? (
                  <span className="review-rejected-badge">✗ REJECTED</span>
                ) : (
                  <span className="review-pending-badge-small">🟡 PENDING</span>
                )}
              </div>

              {/* Canonical Test */}
              {selectedCandidate.canonical_test && (
                <div className="review-detail-section">
                  <span className="review-detail-label">Resolved Test</span>
                  <span className="review-detail-value">{selectedCandidate.canonical_test.display_name}</span>
                </div>
              )}

              {/* Raw Values */}
              <div className="review-detail-section">
                <span className="review-detail-label">Raw Value</span>
                <span className="review-detail-value">
                  {selectedCandidate.value} {selectedCandidate.unit || ''}
                </span>
              </div>

              {/* Normalized Value */}
              {selectedCandidate.normalized_value != null && (
                <div className="review-detail-section">
                  <span className="review-detail-label">Normalized Value</span>
                  <span className="review-detail-value">
                    {selectedCandidate.normalized_value} {selectedCandidate.normalized_unit || ''}
                  </span>
                </div>
              )}

              {/* Reference Range */}
              <div className="review-detail-section">
                <span className="review-detail-label">Reference Range</span>
                <span className="review-detail-value">
                  {selectedCandidate.reference_range || '—'}
                </span>
                {selectedCandidate.normalized_reference_lower != null && (
                  <span className="review-detail-sub">
                    Normalized: {selectedCandidate.normalized_reference_lower} – {selectedCandidate.normalized_reference_upper}
                  </span>
                )}
              </div>

              {/* Abnormality Status */}
              <div className="review-detail-section">
                <span className="review-detail-label">Abnormality</span>
                <span className={`review-detail-abnormality review-detail-abnormality-${selectedCandidate.abnormality_status}`}>
                  {selectedCandidate.abnormality_status}
                </span>
              </div>

              {/* Result Date */}
              {selectedCandidate.result_date && (
                <div className="review-detail-section">
                  <span className="review-detail-label">Result Date</span>
                  <span className="review-detail-value">
                    {selectedCandidate.normalized_result_date || selectedCandidate.result_date}
                  </span>
                </div>
              )}

              {/* Specimen */}
              {selectedCandidate.specimen && (
                <div className="review-detail-section">
                  <span className="review-detail-label">Specimen</span>
                  <span className="review-detail-value">{selectedCandidate.specimen}</span>
                </div>
              )}

              {/* Evidence Section */}
              <div className="review-evidence-section">
                <h5 className="review-evidence-title">Evidence</h5>
                <div className="review-evidence-card">
                  {selectedCandidate.evidence_record ? (
                    <>
                      {selectedCandidate.evidence_record.page_number != null && (
                        <div className="review-evidence-item">
                          <span className="review-evidence-label">Page</span>
                          <span className="review-evidence-value">{selectedCandidate.evidence_record.page_number}</span>
                        </div>
                      )}
                      {selectedCandidate.evidence_record.source_text && (
                        <div className="review-evidence-item">
                          <span className="review-evidence-label">Source Text</span>
                          <blockquote className="review-evidence-quote">
                            {selectedCandidate.evidence_record.source_text}
                          </blockquote>
                        </div>
                      )}
                      {!selectedCandidate.evidence_record.source_text && (
                        <div className="review-evidence-item">
                          <span className="review-evidence-label">AI Evidence</span>
                          <blockquote className="review-evidence-quote review-evidence-unverified">
                            {selectedCandidate.evidence}
                          </blockquote>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="review-evidence-item">
                      <span className="review-evidence-label">Evidence</span>
                      <blockquote className="review-evidence-quote">{selectedCandidate.evidence}</blockquote>
                    </div>
                  )}
                </div>
              </div>

              {/* Verify / Correct Action */}
              {selectedCandidate.verification_status === 'pending' && (
                <div className="review-verify-section">
                  {verifyError && selectedCandidate.id === verifyError && (
                    <div className="review-verify-error">
                      <p>{verifyError}</p>
                    </div>
                  )}
                  {verifySuccess === selectedCandidate.id && (
                    <div className="review-verify-success">
                      <p>✓ Candidate verified successfully.</p>
                    </div>
                  )}
                  <div className="review-actions-row">
                    <button
                      type="button"
                      className="review-verify-button"
                      onClick={() => handleVerify(selectedCandidate.id)}
                      disabled={verifyLoading !== null}
                    >
                      {verifyLoading === selectedCandidate.id ? 'Verifying...' : 'Verify Result'}
                    </button>
                    <button
                      type="button"
                      className="review-correct-button"
                      onClick={() => openCorrectModal(selectedCandidate)}
                    >
                      Correct Values
                    </button>
                    <button
                      type="button"
                      className="review-reject-button"
                      onClick={() => openRejectModal(selectedCandidate)}
                    >
                      Reject
                    </button>
                  </div>
                  <p className="review-verify-hint">
                    Verify confirms the result is accurate. Correct allows fixing extracted values.
                  </p>
                </div>
              )}

              {/* Rejected badge */}
              {selectedCandidate.verification_status === 'rejected' && (
                <div className="review-rejected-section">
                  <span className="review-rejected-badge">✗ REJECTED — Not Trusted</span>
                  {selectedCandidate.rejection_reason && (
                    <div className="review-rejection-reason">
                      <span className="review-detail-label">Rejection Reason</span>
                      <p className="review-rejection-reason-text">
                        {selectedCandidate.rejection_reason}
                      </p>
                    </div>
                  )}
                  <p className="review-verify-hint">
                    This candidate was rejected by a doctor and will not become trusted medical data.
                  </p>
                </div>
              )}

              {/* Corrected badge */}
              {selectedCandidate.verification_status === 'corrected' && (
                <div className="review-corrected-section">
                  <span className="review-corrected-badge">✓ CORRECTED — Trusted</span>
                  <p className="review-verify-hint">
                    This candidate was corrected by a doctor and is now trusted medical data.
                  </p>
                </div>
              )}

              {/* Normalization Status Summary */}
              <div className="review-normalization-summary">
                <h5 className="review-summary-title">Normalization Status</h5>
                <div className="review-summary-grid">
                  <div className="review-summary-item">
                    <span className="review-summary-label">Test Name</span>
                    <span className={`review-summary-status review-summary-status-${selectedCandidate.normalization_status}`}>
                      {selectedCandidate.normalization_status}
                    </span>
                  </div>
                  <div className="review-summary-item">
                    <span className="review-summary-label">Unit</span>
                    <span className={`review-summary-status review-summary-status-${selectedCandidate.unit_normalization_status}`}>
                      {selectedCandidate.unit_normalization_status}
                    </span>
                  </div>
                  <div className="review-summary-item">
                    <span className="review-summary-label">Date</span>
                    <span className={`review-summary-status review-summary-status-${selectedCandidate.date_normalization_status}`}>
                      {selectedCandidate.date_normalization_status}
                    </span>
                  </div>
                  <div className="review-summary-item">
                    <span className="review-summary-label">Ref Range</span>
                    <span className={`review-summary-status review-summary-status-${selectedCandidate.reference_range_normalization_status}`}>
                      {selectedCandidate.reference_range_normalization_status}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      {/* Correction Modal */}
      {showCorrectModal && correctingCandidate && (
        <div className="review-modal-overlay" onClick={closeCorrectModal}>
          <div className="review-modal" onClick={(e) => e.stopPropagation()}>
            <div className="review-modal-header">
              <h3>Correct Candidate Values</h3>
              <button type="button" className="review-modal-close" onClick={closeCorrectModal}>
                ×
              </button>
            </div>

            <div className="review-modal-original">
              <h4>Original Extracted Values</h4>
              <div className="review-modal-original-grid">
                <div><span>Test:</span> {correctingCandidate.test_name}</div>
                <div><span>Value:</span> {correctingCandidate.value} {correctingCandidate.unit || ''}</div>
                {correctingCandidate.normalized_value != null && (
                  <div><span>Normalized:</span> {correctingCandidate.normalized_value} {correctingCandidate.normalized_unit || ''}</div>
                )}
                <div><span>Ref Range:</span> {correctingCandidate.reference_range || '—'}</div>
                {correctingCandidate.result_date && (
                  <div><span>Date:</span> {correctingCandidate.result_date}</div>
                )}
              </div>
            </div>

            <div className="review-modal-form">
              <h4>Corrected Values</h4>
              <p className="review-modal-hint">
                Only fill in fields that need correction. Leave blank to keep the original value.
              </p>

              <div className="review-form-group">
                <label htmlFor="correct-test-name">Test Name</label>
                <input
                  id="correct-test-name"
                  type="text"
                  value={correctForm.test_name}
                  onChange={(e) => setCorrectForm({ ...correctForm, test_name: e.target.value })}
                  placeholder={correctingCandidate.test_name}
                />
              </div>

              <div className="review-form-row">
                <div className="review-form-group">
                  <label htmlFor="correct-value">Value</label>
                  <input
                    id="correct-value"
                    type="text"
                    value={correctForm.value}
                    onChange={(e) => setCorrectForm({ ...correctForm, value: e.target.value })}
                    placeholder={correctingCandidate.value}
                  />
                </div>
                <div className="review-form-group">
                  <label htmlFor="correct-unit">Unit</label>
                  <input
                    id="correct-unit"
                    type="text"
                    value={correctForm.unit}
                    onChange={(e) => setCorrectForm({ ...correctForm, unit: e.target.value })}
                    placeholder={correctingCandidate.unit || ''}
                  />
                </div>
              </div>

              <div className="review-form-group">
                <label htmlFor="correct-range">Reference Range</label>
                <input
                  id="correct-range"
                  type="text"
                  value={correctForm.reference_range}
                  onChange={(e) => setCorrectForm({ ...correctForm, reference_range: e.target.value })}
                  placeholder={correctingCandidate.reference_range || ''}
                />
              </div>

              <div className="review-form-group">
                <label htmlFor="correct-date">Result Date</label>
                <input
                  id="correct-date"
                  type="text"
                  value={correctForm.result_date}
                  onChange={(e) => setCorrectForm({ ...correctForm, result_date: e.target.value })}
                  placeholder={correctingCandidate.result_date || ''}
                />
              </div>

              <div className="review-form-group">
                <label htmlFor="correct-reason">Correction Reason *</label>
                <textarea
                  id="correct-reason"
                  value={correctForm.reason}
                  onChange={(e) => setCorrectForm({ ...correctForm, reason: e.target.value })}
                  placeholder="Why is this correction needed?"
                  rows={3}
                  required
                />
              </div>

              {correctError && (
                <div className="review-modal-error">
                  <p>{correctError}</p>
                </div>
              )}

              <div className="review-modal-actions">
                <button type="button" className="review-modal-cancel" onClick={closeCorrectModal}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="review-correct-submit"
                  onClick={handleCorrect}
                  disabled={correctLoading}
                >
                  {correctLoading ? 'Submitting...' : 'Submit Correction'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Reject Modal */}
      {showRejectModal && rejectingCandidate && (
        <div className="review-modal-overlay" onClick={closeRejectModal}>
          <div className="review-modal" onClick={(e) => e.stopPropagation()}>
            <div className="review-modal-header">
              <h3>Reject Candidate</h3>
              <button type="button" className="review-modal-close" onClick={closeRejectModal}>
                ×
              </button>
            </div>

            <div className="review-modal-form">
              <div className="review-reject-warning">
                <p><strong>Warning:</strong> Rejecting this candidate means it will <strong>not</strong> become trusted medical data. This action cannot be undone.</p>
              </div>

              <div className="review-modal-original">
                <h4>Candidate to Reject</h4>
                <div className="review-modal-original-grid">
                  <div><span>Test:</span> {rejectingCandidate.test_name}</div>
                  <div><span>Value:</span> {rejectingCandidate.value} {rejectingCandidate.unit || ''}</div>
                </div>
              </div>

              <div className="review-form-group">
                <label htmlFor="reject-reason">Rejection Reason *</label>
                <textarea
                  id="reject-reason"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  placeholder="Why is this candidate being rejected?"
                  rows={3}
                  required
                />
              </div>

              {rejectError && (
                <div className="review-modal-error">
                  <p>{rejectError}</p>
                </div>
              )}

              <div className="review-modal-actions">
                <button type="button" className="review-modal-cancel" onClick={closeRejectModal}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="review-reject-submit"
                  onClick={handleReject}
                  disabled={rejectLoading}
                >
                  {rejectLoading ? 'Rejecting...' : 'Reject Candidate'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}

export default DoctorReviewPage
