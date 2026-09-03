import { Link } from 'react-router-dom'
import './CallToAction.css'

function CallToAction() {
  return (
    <section className="cta">
      <div className="container cta-inner">
        <h2 className="cta-heading">Take Control of Your Medical Information</h2>
        <p className="cta-text">
          Organize your reports, understand your information, and keep your verified
          health history in one place.
        </p>
        <Link to="/signup" className="btn btn-cta">
          Get Started
        </Link>
      </div>
    </section>
  )
}

export default CallToAction
