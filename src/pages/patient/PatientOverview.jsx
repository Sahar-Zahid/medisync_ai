import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'
import DashboardCard from '../../components/dashboard/DashboardCard.jsx'
import {
  ReportsIcon,
  DoctorsIcon,
  AppointmentsIcon,
  ProfileIcon,
} from '../../components/dashboard/icons.jsx'
import './PatientOverview.css'

// Quick-action shortcuts to the existing patient pages. Purely
// navigational — no data of their own, so adding/reordering an entry
// here never needs a backend change.
const QUICK_ACTIONS = [
  { label: 'View Reports', to: '/patient/reports', icon: ReportsIcon },
  { label: 'Find a Doctor', to: '/patient/doctors', icon: DoctorsIcon },
  { label: 'Book Appointment', to: '/patient/appointments', icon: AppointmentsIcon },
  { label: 'Edit Profile', to: '/patient/profile', icon: ProfileIcon },
]

function PatientOverview() {
  const { user } = useAuth()
  const firstName = user?.full_name?.trim().split(' ')[0] ?? ''

  return (
    <div className="patient-overview">
      <div className="patient-overview-intro">
        <span className="eyebrow">Patient Dashboard</span>
        <h1>{firstName ? `Welcome back, ${firstName}` : 'Welcome back'}</h1>
        <p className="section-intro">
          MediSync will help you keep your medical reports organized, easier to understand,
          and connected with your care team — all in one place.
        </p>
      </div>

      {/*
        Summary strip: an honest reflection of the current state (nothing
        is fetched yet, so there's nothing to count) rather than fabricated
        numbers. Once appointments/reports have real endpoints, these three
        figures should be replaced with real fetched counts.
      */}
      <div className="patient-overview-summary">
        <div className="patient-overview-summary-item">
          <span className="patient-overview-summary-value">0</span>
          <span className="patient-overview-summary-label">Upcoming Appointments</span>
        </div>
        <div className="patient-overview-summary-item">
          <span className="patient-overview-summary-value">0</span>
          <span className="patient-overview-summary-label">Recent Reports</span>
        </div>
        <div className="patient-overview-summary-item">
          <span className="patient-overview-summary-value">0</span>
          <span className="patient-overview-summary-label">Connected Doctors</span>
        </div>
      </div>

      <div className="patient-overview-grid">
        <DashboardCard
          title="Upcoming Appointments"
          emptyState="No upcoming appointments."
          actionLabel="View appointments"
          actionTo="/patient/appointments"
        />
        <DashboardCard
          title="Recent Reports"
          emptyState="No recent reports."
          actionLabel="View reports"
          actionTo="/patient/reports"
        />
        <DashboardCard title="My Doctors" emptyState="No doctors connected yet." />
      </div>

      <section className="patient-overview-quick-actions">
        <h2 className="patient-overview-quick-actions-title">Quick Actions</h2>
        <div className="patient-overview-quick-actions-grid">
          {QUICK_ACTIONS.map(({ label, to, icon: Icon }) => (
            <Link key={label} to={to} className="patient-overview-quick-action">
              <Icon />
              <span>{label}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}

export default PatientOverview
