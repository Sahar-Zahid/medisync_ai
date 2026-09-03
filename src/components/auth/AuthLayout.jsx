import { Link } from 'react-router-dom'
import './AuthLayout.css'

function AuthLayout({ message, children }) {
  return (
    <div className="auth-page">
      <div className="auth-brand">
        <Link to="/" className="auth-brand-logo">
          <span className="auth-brand-mark" aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 22 22" fill="none">
              <rect width="22" height="22" rx="6" fill="#ffffff" fillOpacity="0.14" />
              <path d="M11 5v12M5 11h12" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
            </svg>
          </span>
          <span className="auth-brand-text">MediSync</span>
        </Link>

        <p className="auth-brand-message">{message}</p>

        <div className="auth-brand-visual" aria-hidden="true">
          <div className="av-card">
            <div className="av-card-header">
              <span className="av-dot" />
              <span className="av-dot" />
              <span className="av-dot" />
            </div>
            <div className="av-line av-line-w80" />
            <div className="av-line av-line-w60" />
            <div className="av-line av-line-w70" />
            <div className="av-badge">
              <span className="av-badge-icon">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8.5l3 3 7-7" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span>Verified</span>
            </div>
          </div>
        </div>
      </div>

      <div className="auth-panel">
        <div className="auth-panel-inner">{children}</div>
      </div>
    </div>
  )
}

export default AuthLayout
