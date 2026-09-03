import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import {
  DashboardIcon,
  ReportsIcon,
  TrustedResultsIcon,
  HistoryIcon,
  DoctorsIcon,
  AppointmentsIcon,
  ProfileIcon,
  LogoutIcon,
} from './icons.jsx'
import './DashboardSidebar.css'

const NAV_ITEMS = [
  { label: 'Dashboard', to: '/patient', icon: DashboardIcon, end: true },
  { label: 'Medical Reports', to: '/patient/reports', icon: ReportsIcon },
  { label: 'Trusted Results', to: '/patient/results', icon: TrustedResultsIcon },
  { label: 'Results History', to: '/patient/history', icon: HistoryIcon },
  { label: 'Doctors', to: '/patient/doctors', icon: DoctorsIcon },
  { label: 'Appointments', to: '/patient/appointments', icon: AppointmentsIcon },
  { label: 'Profile', to: '/patient/profile', icon: ProfileIcon },
]

/**
 * Sidebar navigation for the patient dashboard. On desktop it's a fixed
 * left column; on smaller screens it becomes a slide-in drawer controlled
 * by `isOpen`/`onClose` (see PatientDashboardLayout).
 */
function DashboardSidebar({ isOpen, onClose }) {
  const { logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    // Calls the real logout endpoint (clears the HttpOnly session cookie
    // server-side) and clears the frontend's own auth state — not just a
    // route change pretending to be a logout.
    await logout()
    navigate('/login')
  }

  return (
    <>
      {isOpen && (
        <div className="dashboard-sidebar-backdrop" onClick={onClose} aria-hidden="true" />
      )}

      <aside className={`dashboard-sidebar ${isOpen ? 'is-open' : ''}`}>
        <nav aria-label="Patient dashboard navigation">
          <ul className="dashboard-nav-list">
            {NAV_ITEMS.map(({ label, to, icon: Icon, end }) => (
              <li key={label}>
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `dashboard-nav-link ${isActive ? 'is-active' : ''}`
                  }
                  onClick={onClose}
                >
                  <Icon />
                  <span>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <button
          type="button"
          className="dashboard-nav-link dashboard-logout"
          onClick={handleLogout}
        >
          <LogoutIcon />
          <span>Logout</span>
        </button>
      </aside>
    </>
  )
}

export default DashboardSidebar
