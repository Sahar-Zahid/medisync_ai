import { Link } from 'react-router-dom'
import './DashboardCard.css'

/**
 * Visual-only placeholder card. Never wired to real or fake data — just
 * a titled section with a clearly-marked empty state.
 *
 * `actionLabel`/`actionTo` are optional — omitting them renders exactly
 * the plain title + empty-state card this component always has, so
 * every existing usage (PatientOverview's three cards) keeps working
 * unchanged.
 */
function DashboardCard({ title, emptyState, actionLabel, actionTo }) {
  return (
    <section className="dashboard-card">
      <h2 className="dashboard-card-title">{title}</h2>
      <p className="dashboard-card-empty">{emptyState}</p>
      {actionLabel && actionTo && (
        <Link to={actionTo} className="dashboard-card-action">
          {actionLabel}
        </Link>
      )}
    </section>
  )
}

export default DashboardCard
