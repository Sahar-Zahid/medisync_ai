import { Link } from 'react-router-dom'
import './Footer.css'

const FOOTER_LINKS = [
  { label: 'Home', to: '/#home' },
  { label: 'About', to: '/#about' },
  { label: 'How It Works', to: '/#how-it-works' },
  { label: 'Contact', to: '/#contact' },
]

function Footer() {
  return (
    <footer id="contact" className="footer">
      <div className="container footer-inner">
        <Link to="/" className="footer-brand">
          <span className="footer-logo-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 22 22" fill="none">
              <rect width="22" height="22" rx="6" fill="#ffffff" fillOpacity="0.12" />
              <path d="M11 5v12M5 11h12" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
            </svg>
          </span>
          <span className="footer-brand-text">MediSync</span>
        </Link>

        <nav aria-label="Footer">
          <ul className="footer-links">
            {FOOTER_LINKS.map((link) => (
              <li key={link.label}>
                <Link to={link.to}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </nav>

        <p className="footer-copy">© 2026 MediSync</p>
      </div>
    </footer>
  )
}

export default Footer
