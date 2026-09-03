import './Features.css'

const FEATURES = [
  {
    title: 'Medical Report Management',
    text: 'Keep every report organized and accessible in one secure place.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <rect x="4" y="2.5" width="14" height="17" rx="2" stroke="currentColor" strokeWidth="1.6" />
        <path d="M7.5 7h7M7.5 10.5h7M7.5 14h4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    title: 'AI-Assisted Extraction',
    text: 'AI helps surface key values from reports so nothing gets missed.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <path
          d="M11 3l2 4.2 4.6.6-3.3 3.2.8 4.6L11 13.4 6.9 15.6l.8-4.6L4.4 7.8 9 7.2 11 3z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    title: 'Doctor Verification',
    text: 'Extracted results are reviewed and confirmed by real doctors.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <circle cx="11" cy="11" r="8.2" stroke="currentColor" strokeWidth="1.6" />
        <path d="M7.3 11.3l2.5 2.5 5-5.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    title: 'Health Trends & Timeline',
    text: 'See how key values change over time in one clear view.',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <path d="M3.5 18V4M3.5 18h15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        <path d="M6.5 14l3-3.5 2.6 2 4.4-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
]

function Features() {
  return (
    <section id="features" className="features">
      <div className="container">
        <div className="features-header">
          <span className="eyebrow">Platform</span>
          <h2 className="section-heading">Built around your medical records</h2>
        </div>

        <div className="features-grid">
          {FEATURES.map((feature) => (
            <div className="feature-card" key={feature.title}>
              <div className="feature-icon">{feature.icon}</div>
              <h3 className="feature-title">{feature.title}</h3>
              <p className="feature-text">{feature.text}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default Features
