import { useEffect, useState } from 'react'
import { TrustedResultsError, fetchTrustedResultsHistory } from '../../services/resultService.js'
import { TrustedResultsIcon } from '../../components/dashboard/icons.jsx'
import '../patient/ResultsPage.css'
import './HistoryPage.css'

const STATUS_LABELS = {
  verified: 'Verified',
  corrected: 'Corrected',
}

const ABNORMALITY_LABELS = {
  normal: 'Normal',
  high: 'High',
  low: 'Low',
  critical: 'Critical',
  unresolved: 'Not classified',
}

function formatValue(result) {
  const value = result.normalized_value ?? result.raw_value
  return result.normalized_unit ? `${value} ${result.normalized_unit}` : `${value}`
}

function formatReferenceRange(result) {
  const { reference_range_lower: lower, reference_range_upper: upper } = result
  if (lower == null && upper == null) return '—'
  if (lower != null && upper != null) return `${lower} – ${upper}`
  if (lower != null) return `≥ ${lower}`
  return `≤ ${upper}`
}

function formatDateHeading(dateString) {
  if (!dateString) return 'Date unknown'
  const parsed = new Date(`${dateString}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return dateString
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Group the already-sorted (newest-first, per the backend's deterministic
 * ordering) results list into consecutive date buckets, preserving order.
 * Results with no result_date are grouped together under "Date unknown".
 */
function groupByDate(results) {
  const groups = []
  let currentKey = undefined
  let currentGroup = null

  for (const result of results) {
    const key = result.result_date ?? null
    if (key !== currentKey || currentGroup === null) {
      currentGroup = { dateKey: key, items: [] }
      groups.push(currentGroup)
      currentKey = key
    }
    currentGroup.items.push(result)
  }

  return groups
}

function HistoryPage() {
  const [results, setResults] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let isMounted = true

    fetchTrustedResultsHistory()
      .then((data) => {
        if (!isMounted) return
        setResults(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof TrustedResultsError
            ? error.message
            : 'Something went wrong loading your results history. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [])

  const groups = groupByDate(results)

  return (
    <div className="results-page history-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>Results History</h1>
      <p className="section-intro">
        A chronological timeline of the test results a doctor has personally reviewed and
        confirmed — newest first.
      </p>

      {isLoading && <p className="dashboard-card-empty">Loading your results history…</p>}

      {!isLoading && loadError && (
        <p className="field-error results-load-error">{loadError}</p>
      )}

      {!isLoading && !loadError && results.length === 0 && (
        <p className="dashboard-card-empty results-empty-state">
          You don't have any doctor-reviewed results yet. Once a doctor verifies a result from
          one of your uploaded reports, it will appear here on your timeline.
        </p>
      )}

      {!isLoading && !loadError && results.length > 0 && (
        <div className="history-timeline">
          {groups.map((group) => (
            <div className="history-date-group" key={group.dateKey ?? 'unknown'}>
              <div className="history-date-heading">
                <span className="history-date-dot" aria-hidden="true" />
                <h2>{formatDateHeading(group.dateKey)}</h2>
              </div>

              <div className="history-date-items">
                {group.items.map((result) => (
                  <div key={result.id} className="dashboard-card result-card history-card">
                    <div className="result-card-header">
                      <span className="result-card-icon" aria-hidden="true">
                        <TrustedResultsIcon />
                      </span>
                      <div className="result-card-title">
                        <h2>{result.canonical_test?.display_name ?? result.test_name}</h2>
                        <span className={`result-status-badge result-status-${result.status}`}>
                          {STATUS_LABELS[result.status] ?? result.status}
                        </span>
                      </div>
                      <span
                        className={`result-abnormality-badge result-abnormality-${result.abnormality_status}`}
                      >
                        {ABNORMALITY_LABELS[result.abnormality_status] ?? result.abnormality_status}
                      </span>
                    </div>

                    <dl className="result-card-details">
                      <div>
                        <dt>Value</dt>
                        <dd>{formatValue(result)}</dd>
                      </div>
                      <div>
                        <dt>Reference range</dt>
                        <dd>{formatReferenceRange(result)}</dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default HistoryPage
