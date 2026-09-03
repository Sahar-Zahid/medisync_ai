import './HowItWorks.css'

const STEPS = [
  {
    number: '01',
    title: 'Upload Report',
    text: 'Patients upload their medical reports securely.',
  },
  {
    number: '02',
    title: 'AI Extracts Information',
    text: 'AI helps identify useful information from the uploaded report.',
  },
  {
    number: '03',
    title: 'Doctor Verifies',
    text: 'Doctors review the extracted information before it becomes trusted patient data.',
  },
]

function HowItWorks() {
  return (
    <section id="how-it-works" className="how">
      <div className="container">
        <div className="how-header">
          <span className="eyebrow">How It Works</span>
          <h2 className="section-heading">From upload to verified record, in three steps</h2>
        </div>

        <div className="how-steps">
          {STEPS.map((step, index) => (
            <div className="how-step" key={step.title}>
              <div className="how-step-number">{step.number}</div>
              <h3 className="how-step-title">{step.title}</h3>
              <p className="how-step-text">{step.text}</p>
              {index < STEPS.length - 1 && <span className="how-step-connector" aria-hidden="true" />}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default HowItWorks
