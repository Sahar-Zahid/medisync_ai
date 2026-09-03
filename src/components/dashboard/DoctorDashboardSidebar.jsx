import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import {
  DashboardIcon,
  DoctorsIcon,
  ReportsIcon,
  ProfileIcon,
  LogoutIcon,
} from './icons.jsx'
import './DashboardSidebar.css'

const NAV_ITEMS = [
  { label: 'Dashboard', to: '/doctor', icon: DashboardIcon, end: true },
  { label: 'Triage', to: '/doctor/triage', icon: ReportsIcon },
  { label: 'My Patients', to: '/doctor/patients', icon: DoctorsIcon },
  { label: 'Profile', to: '/doctor/profile', icon: ProfileIcon },
]

/**
 * Sidebar navigation for the doctor dashboard. On desktop it's a fixed
 * left column; on smaller screens it becomes a slide-in drawer controlled
 * by `isOpen`/`onClose` (see DoctorDashboardLayout).
 */
function DoctorDashboardSidebar({ isOpen, onClose }) {
  const { logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <>
      {isOpen && (
        <div className="dashboard-sidebar-backdrop" onClick={onClose} aria-hidden="true" />
      )}

      <aside className={`dashboard-sidebar ${isOpen ? 'is-open' : ''}`}>
        <nav aria-label="Doctor dashboard navigation">
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

export default DoctorDashboardSidebar
