import { useState } from 'react'
import { Link } from 'react-router-dom'
import './Navbar.css'

const NAV_LINKS = [
  { label: 'Home', to: '/#home' },
  { label: 'About', to: '/#about' },
  { label: 'How It Works', to: '/#how-it-works' },
  { label: 'Contact', to: '/#contact' },
]

function Navbar() {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <header className="navbar">
      <div className="container navbar-inner">
        <Link to="/" className="navbar-logo" onClick={() => setMenuOpen(false)}>
          <span className="navbar-logo-mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
              <rect width="22" height="22" rx="6" fill="#0E6E5C" />
              <path d="M11 5v12M5 11h12" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
            </svg>
          </span>
          <span className="navbar-logo-text">MediSync</span>
        </Link>

        <nav className={`navbar-links ${menuOpen ? 'is-open' : ''}`} aria-label="Primary">
          <ul>
            {NAV_LINKS.map((link) => (
              <li key={link.label}>
                <Link to={link.to} onClick={() => setMenuOpen(false)}>
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="navbar-actions">
          <Link to="/login" className="btn btn-primary navbar-login">
            Login
          </Link>
          <button
            type="button"
            className={`navbar-toggle ${menuOpen ? 'is-open' : ''}`}
            aria-label="Toggle navigation menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span />
            <span />
            <span />
          </button>
        </div>
      </div>
    </header>
  )
}

export default Navbar
