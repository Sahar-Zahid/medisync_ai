import { useState } from 'react'
import { Link } from 'react-router-dom'
import AuthLayout from '../components/auth/AuthLayout.jsx'
import FormField from '../components/auth/FormField.jsx'
import RoleToggle from '../components/auth/RoleToggle.jsx'
import { isValidEmail } from '../utils/validation.js'
import { signup, SignupError } from '../services/authService.js'
import './AuthForm.css'

const INITIAL_FORM = {
  fullName: '',
  email: '',
  password: '',
  confirmPassword: '',
}

function SignupPage() {
  const [form, setForm] = useState(INITIAL_FORM)
  const [role, setRole] = useState('')
  const [errors, setErrors] = useState({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [accountCreated, setAccountCreated] = useState(false)

  function handleChange(event) {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  function validate() {
    const nextErrors = {}

    if (!form.fullName.trim()) {
      nextErrors.fullName = 'Please enter your full name.'
    }

    if (!form.email.trim()) {
      nextErrors.email = 'Please enter your email address.'
    } else if (!isValidEmail(form.email)) {
      nextErrors.email = 'Please enter a valid email address.'
    }

    if (!form.password) {
      nextErrors.password = 'Please create a password.'
    }

    if (!form.confirmPassword) {
      nextErrors.confirmPassword = 'Please confirm your password.'
    } else if (form.password && form.confirmPassword !== form.password) {
      nextErrors.confirmPassword = 'Passwords do not match.'
    }

    if (!role) {
      nextErrors.role = 'Please select whether you are a patient or a doctor.'
    }

    return nextErrors
  }

  async function handleSubmit(event) {
    event.preventDefault()

    const nextErrors = validate()
    setErrors(nextErrors)
    setSubmitError('')

    if (Object.keys(nextErrors).length > 0) {
      return
    }

    setIsSubmitting(true)
    try {
      // Only fullName, email, password, and role are ever sent — the
      // confirmPassword field is a frontend-only check and is not
      // transmitted. role is already "patient" or "doctor" (see
      // RoleToggle), matching the backend's expected values exactly.
      await signup({
        fullName: form.fullName.trim(),
        email: form.email.trim(),
        password: form.password,
        role,
      })
      setAccountCreated(true)
    } catch (error) {
      const message =
        error instanceof SignupError
          ? error.message
          : 'Unable to connect to MediSync. Please try again.'
      setSubmitError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthLayout message="Join MediSync to keep your medical reports organized, understandable, and doctor-verified.">
      <span className="eyebrow">Get Started</span>
      <h1 className="auth-heading">Create Account</h1>

      <form className="auth-form" onSubmit={handleSubmit} noValidate>
        <FormField
          label="Full Name"
          name="fullName"
          value={form.fullName}
          onChange={handleChange}
          error={errors.fullName}
          placeholder="Your full name"
          autoComplete="name"
        />
        <FormField
          label="Email"
          type="email"
          name="email"
          value={form.email}
          onChange={handleChange}
          error={errors.email}
          placeholder="you@example.com"
          autoComplete="email"
        />
        <FormField
          label="Password"
          type="password"
          name="password"
          value={form.password}
          onChange={handleChange}
          error={errors.password}
          placeholder="Create a password"
          autoComplete="new-password"
        />
        <FormField
          label="Confirm Password"
          type="password"
          name="confirmPassword"
          value={form.confirmPassword}
          onChange={handleChange}
          error={errors.confirmPassword}
          placeholder="Re-enter your password"
          autoComplete="new-password"
        />

        <RoleToggle
          label="Create account as:"
          value={role}
          onChange={setRole}
          error={errors.role}
          showSelection
        />

        <button
          type="submit"
          className="btn btn-primary auth-submit"
          disabled={isSubmitting}
        >
          {isSubmitting ? 'Creating account...' : 'Create Account'}
        </button>

        {accountCreated && (
          <p className="auth-status" role="status">
            Account created successfully. You can now{' '}
            <Link to="/login">log in</Link>.
          </p>
        )}

        {submitError && (
          <p className="auth-status auth-status-error" role="alert">
            {submitError}
          </p>
        )}
      </form>

      <p className="auth-switch">
        Already have an account? <Link to="/login">Login</Link>
      </p>
    </AuthLayout>
  )
}

export default SignupPage
