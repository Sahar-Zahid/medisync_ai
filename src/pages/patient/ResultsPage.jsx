import { useCallback, useEffect, useState } from 'react'
import { TrustedResultsError, fetchTrustedResults } from '../../services/resultService.js'
import { ResultSummaryError, fetchResultSummary } from '../../services/resultSummaryService.js'
import { AiSparkleIcon, TrustedResultsIcon } from '../../components/dashboard/icons.jsx'
import './ResultsPage.css'

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

function AiSummaryCard() {
  const [summary, setSummary] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  const loadSummary = useCallback(() => {
    let isMounted = true
    setIsLoading(true)
    setLoadError('')

    fetchResultSummary()
      .then((data) => {
        if (!isMounted) return
        setSummary(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof ResultSummaryError
            ? error.message
            : 'Something went wrong generating your AI summary. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => {
    const cleanup = loadSummary()
    return cleanup
    // refreshKey deliberately retriggers this effect on manual refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadSummary, refreshKey])

  return (
    <section className="dashboard-card ai-summary-card" aria-label="AI summary of your trusted results">
      <div className="ai-summary-header">
        <span className="ai-summary-icon" aria-hidden="true">
          <AiSparkleIcon />
        </span>
        <div className="ai-summary-title">
          <h2>AI Summary</h2>
          <span className="ai-summary-badge">AI-generated · not a diagnosis</span>
        </div>
        <button
          type="button"
          className="ai-summary-refresh"
          onClick={() => setRefreshKey((key) => key + 1)}
          disabled={isLoading}
        >
          {isLoading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {isLoading && <p className="dashboard-card-empty">Generating your AI summary…</p>}

      {!isLoading && loadError && <p className="field-error ai-summary-error">{loadError}</p>}

      {!isLoading && !loadError && summary && !summary.has_trusted_results && (
        <p className="dashboard-card-empty ai-summary-empty">{summary.observations[0]}</p>
      )}

      {!isLoading && !loadError && summary && summary.has_trusted_results && (
        <>
          <p className="ai-summary-scope">
            Based on {summary.result_count} doctor-reviewed result
            {summary.result_count === 1 ? '' : 's'}.
          </p>
          {summary.observations.length > 0 ? (
            <ul className="ai-summary-observations">
              {summary.observations.map((observation, index) => (
                <li key={index}>{observation}</li>
              ))}
            </ul>
          ) : (
            <p className="dashboard-card-empty">No notable observations for your current results.</p>
          )}
        </>
      )}

      {!isLoading && !loadError && summary && (
        <p className="ai-summary-disclaimer">{summary.disclaimer}</p>
      )}
    </section>
  )
}

function ResultsPage() {
  const [results, setResults] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let isMounted = true

    fetchTrustedResults()
      .then((data) => {
        if (!isMounted) return
        setResults(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof TrustedResultsError
            ? error.message
            : 'Something went wrong loading your trusted results. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="results-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>Trusted Results</h1>
      <p className="section-intro">
        These are the test results a doctor has personally reviewed and confirmed — not
        AI-extracted candidates awaiting review.
      </p>

      <AiSummaryCard />

      <h2 className="results-list-heading">Your Results</h2>

      {isLoading && <p className="dashboard-card-empty">Loading your trusted results…</p>}

      {!isLoading && loadError && (
        <p className="field-error results-load-error">{loadError}</p>
      )}

      {!isLoading && !loadError && results.length === 0 && (
        <p className="dashboard-card-empty results-empty-state">
          You don't have any doctor-reviewed results yet. Once a doctor verifies a result from
          one of your uploaded reports, it will appear here.
        </p>
      )}

      {!isLoading && !loadError && results.length > 0 && (
        <div className="results-list">
          {results.map((result) => (
            <div key={result.id} className="dashboard-card result-card">
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
                <div>
                  <dt>Result date</dt>
                  <dd>{result.result_date ?? '—'}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default ResultsPage
