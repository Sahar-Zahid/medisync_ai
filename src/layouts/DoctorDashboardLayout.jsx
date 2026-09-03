import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import DoctorDashboardHeader from '../components/dashboard/DoctorDashboardHeader.jsx'
import DoctorDashboardSidebar from '../components/dashboard/DoctorDashboardSidebar.jsx'
import './DashboardLayout.css'

/**
 * Shell shared by every /doctor/* page: sticky header on top, sidebar
 * navigation on the left (a slide-in drawer on small screens), and the
 * active page rendered via <Outlet /> on the right.
 */
function DoctorDashboardLayout() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  return (
    <div className="dashboard-shell">
      <DoctorDashboardHeader onToggleSidebar={() => setIsSidebarOpen((open) => !open)} />
      <div className="dashboard-body">
        <DoctorDashboardSidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />
        <main className="dashboard-main">
          <div className="dashboard-main-inner">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}

export default DoctorDashboardLayout
