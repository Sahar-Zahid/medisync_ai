import { Link } from 'react-router-dom'
import './Hero.css'

function Hero() {
  return (
    <section id="home" className="hero">
      <div className="container hero-inner">
        <div className="hero-copy">
          <span className="eyebrow">Patients · Reports · Doctors</span>
          <h1 className="hero-heading">Your Medical Reports, Made Clear</h1>
          <p className="hero-text">
            MediSync helps patients organize their medical reports and makes important
            health information easier to understand, while giving doctors a secure way
            to review and verify extracted results.
          </p>
          <div className="hero-actions">
            <Link to="/signup" className="btn btn-primary">
              Get Started
            </Link>
            <Link to="/#how-it-works" className="btn btn-secondary">
              Learn More
            </Link>
          </div>
        </div>

        <div className="hero-visual" aria-hidden="true">
          <div className="hero-visual-stack">
            <div className="hv-card hv-card-report">
              <div className="hv-card-header">
                <span className="hv-dot" />
                <span className="hv-dot" />
                <span className="hv-dot" />
                <span className="hv-card-title">Lab Report</span>
              </div>
              <div className="hv-line hv-line-w80" />
              <div className="hv-line hv-line-w60" />
              <div className="hv-line hv-line-w70" />
              <div className="hv-divider" />
              <div className="hv-stat-row">
                <span className="hv-stat-label">Glucose</span>
                <span className="hv-stat-value">96 mg/dL</span>
              </div>
              <div className="hv-stat-row">
                <span className="hv-stat-label">Hemoglobin</span>
                <span className="hv-stat-value">13.8 g/dL</span>
              </div>
            </div>

            <div className="hv-card hv-card-trend">
              <span className="hv-card-title hv-card-title-sm">Health Trend</span>
              <div className="hv-chart">
                <span style={{ height: '38%' }} />
                <span style={{ height: '55%' }} />
                <span style={{ height: '48%' }} />
                <span style={{ height: '72%' }} />
                <span style={{ height: '64%' }} />
                <span style={{ height: '82%' }} />
              </div>
            </div>

            <div className="hv-path">
              <span className="hv-path-dot" />
            </div>

            <div className="hv-badge">
              <span className="hv-badge-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8.5l3 3 7-7" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span className="hv-badge-text">
                Verified by
                <strong> Dr. Iqbal</strong>
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default Hero
