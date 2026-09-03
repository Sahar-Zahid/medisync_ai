import './PlaceholderPage.css'

/**
 * Shared shell for the sidebar routes that only exist as placeholders so
 * far (Reports, Doctors, Appointments, Profile) — title, one-line
 * description, and a clearly-marked empty state. No real functionality,
 * no fake data.
 */
function PlaceholderPage({ title, description, emptyState }) {
  return (
    <div className="dashboard-placeholder-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>{title}</h1>
      <p className="section-intro">{description}</p>
      <p className="dashboard-card-empty">{emptyState}</p>
    </div>
  )
}

export default PlaceholderPage
