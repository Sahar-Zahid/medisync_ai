import './RoleToggle.css'

function RoleToggle({ label, value, onChange, error, showSelection = false }) {
  const options = [
    { id: 'patient', text: 'Patient' },
    { id: 'doctor', text: 'Doctor' },
  ]

  const selectedOption = options.find((option) => option.id === value)

  return (
    <div className="role-field">
      <span className="role-field-label">{label}</span>
      <div className="role-toggle" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={`role-option ${value === option.id ? 'is-selected' : ''}`}
            aria-pressed={value === option.id}
            onClick={() => onChange(option.id)}
          >
            {option.text}
          </button>
        ))}
      </div>
      {error && <p className="field-error">{error}</p>}
      {showSelection && selectedOption && (
        <p className="role-selection-preview">Selected role: {selectedOption.text}</p>
      )}
    </div>
  )
}

export default RoleToggle
