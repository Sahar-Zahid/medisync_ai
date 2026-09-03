import { Navigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext.jsx'

// Where a user with a given database role belongs, used to redirect them
// away from a dashboard that isn't theirs (rather than just blocking
// them with nowhere to go).
const ROLE_HOME_ROUTES = {
  patient: '/patient',
  doctor: '/doctor',
}

/**
 * Route guard: renders children only if GET /auth/me confirmed an active
 * session, optionally further restricted to specific roles.
 *
 * The role check always uses the database role returned by /auth/me
 * (via AuthContext) — never anything decided at login time — so a
 * doctor account cannot reach the patient dashboard (or vice versa)
 * simply by navigating to its URL.
 */
function RequireAuth({ children, allowedRoles }) {
  const { user, isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return null
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  if (allowedRoles && !allowedRoles.includes(user.role)) {
    return <Navigate to={ROLE_HOME_ROUTES[user.role] ?? '/login'} replace />
  }

  return children
}

export default RequireAuth
