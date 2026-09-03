import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import DashboardHeader from '../components/dashboard/DashboardHeader.jsx'
import DashboardSidebar from '../components/dashboard/DashboardSidebar.jsx'
import './DashboardLayout.css'

/**
 * Shell shared by every /patient/* page: sticky header on top, sidebar
 * navigation on the left (a slide-in drawer on small screens), and the
 * active page rendered via <Outlet /> on the right.
 */
function PatientDashboardLayout() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  return (
    <div className="dashboard-shell">
      <DashboardHeader onToggleSidebar={() => setIsSidebarOpen((open) => !open)} />
      <div className="dashboard-body">
        <DashboardSidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />
        <main className="dashboard-main">
          <div className="dashboard-main-inner">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}

export default PatientDashboardLayout
