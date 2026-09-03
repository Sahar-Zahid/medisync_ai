import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import { MenuIcon } from './icons.jsx'
import './DashboardHeader.css'

/**
 * Simple sticky dashboard header for doctor view: MediSync branding,
 * mobile menu toggle, and the authenticated doctor's name.
 */
function DoctorDashboardHeader({ onToggleSidebar }) {
  const { user } = useAuth()

  return (
    <header className="dashboard-header">
      <div className="dashboard-header-inner">
        <button
          type="button"
          className="dashboard-menu-toggle"
          aria-label="Toggle navigation menu"
          onClick={onToggleSidebar}
        >
          <MenuIcon />
        </button>

        <Link to="/doctor" className="dashboard-logo">
          <span className="dashboard-logo-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
              <rect width="22" height="22" rx="6" fill="#0E6E5C" />
              <path d="M11 5v12M5 11h12" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
            </svg>
          </span>
          <span className="dashboard-logo-text">MediSync</span>
        </Link>

        <div className="dashboard-header-user">
          <span className="dashboard-user-name">{user?.full_name ?? ''}</span>
        </div>
      </div>
    </header>
  )
}

export default DoctorDashboardHeader
