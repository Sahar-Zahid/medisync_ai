import './TrustSection.css'

function TrustSection() {
  return (
    <section id="about" className="trust">
      <div className="container trust-inner">
        <div className="trust-copy">
          <span className="eyebrow">What MediSync Is</span>
          <h2 className="section-heading">
            One platform for organizing reports, extracting information, and doctor
            verification
          </h2>
          <p className="section-intro">
            MediSync combines medical report organization, AI-assisted information
            extraction, and doctor verification in one platform — helping patients keep
            a clearer, more organized view of their own records.
          </p>
        </div>

        <div className="trust-pillars">
          <div className="trust-pillar">
            <span className="trust-pillar-label">Organize</span>
            <p>Reports stay structured and easy to find in one place.</p>
          </div>
          <div className="trust-pillar">
            <span className="trust-pillar-label">Extract</span>
            <p>AI assists in identifying key details from each report.</p>
          </div>
          <div className="trust-pillar">
            <span className="trust-pillar-label">Verify</span>
            <p>Doctors review extracted information before it's trusted.</p>
          </div>
        </div>
      </div>
    </section>
  )
}

export default TrustSection
