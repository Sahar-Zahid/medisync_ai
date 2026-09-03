import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext.jsx'
import RequireAuth from './components/auth/RequireAuth.jsx'
import LandingPage from './pages/LandingPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import SignupPage from './pages/SignupPage.jsx'
import PatientDashboardLayout from './layouts/PatientDashboardLayout.jsx'
import PatientOverview from './pages/patient/PatientOverview.jsx'
import ReportsPage from './pages/patient/ReportsPage.jsx'
import ResultsPage from './pages/patient/ResultsPage.jsx'
import HistoryPage from './pages/patient/HistoryPage.jsx'
import DoctorsPage from './pages/patient/DoctorsPage.jsx'
import DoctorDetailsPage from './pages/patient/DoctorDetailsPage.jsx'
import AppointmentsPage from './pages/patient/AppointmentsPage.jsx'
import ProfilePage from './pages/patient/ProfilePage.jsx'
import DoctorDashboardLayout from './layouts/DoctorDashboardLayout.jsx'
import MyPatientsPage from './pages/doctor/MyPatientsPage.jsx'
import PatientReportsPage from './pages/doctor/PatientReportsPage.jsx'
import DoctorReviewPage from './pages/doctor/DoctorReviewPage.jsx'
import TriagePage from './pages/doctor/TriagePage.jsx'

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />

        <Route
          path="/patient"
          element={
            <RequireAuth allowedRoles={['patient']}>
              <PatientDashboardLayout />
            </RequireAuth>
          }
        >
          <Route index element={<PatientOverview />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="doctors" element={<DoctorsPage />} />
          <Route path="doctors/:doctorId" element={<DoctorDetailsPage />} />
          <Route path="appointments" element={<AppointmentsPage />} />
          <Route path="profile" element={<ProfilePage />} />
        </Route>

        <Route
          path="/doctor"
          element={
            <RequireAuth allowedRoles={['doctor']}>
              <DoctorDashboardLayout />
            </RequireAuth>
          }
        >
          <Route index element={<MyPatientsPage />} />
          <Route path="triage" element={<TriagePage />} />
          <Route path="patients" element={<MyPatientsPage />} />
          <Route path="patients/:patientId" element={<PatientReportsPage />} />
          <Route path="patients/:patientId/reports/:reportId/review" element={<DoctorReviewPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  )
}

export default App
