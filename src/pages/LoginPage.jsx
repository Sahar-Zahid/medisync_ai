import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AuthLayout from '../components/auth/AuthLayout.jsx'
import FormField from '../components/auth/FormField.jsx'
import RoleToggle from '../components/auth/RoleToggle.jsx'
import { isValidEmail } from '../utils/validation.js'
import { LoginError } from '../services/authService.js'
import { useAuth } from '../context/AuthContext.jsx'
import './AuthForm.css'

const INITIAL_FORM = { email: '', password: '' }

function LoginPage() {
  const [form, setForm] = useState(INITIAL_FORM)
  const [role, setRole] = useState('')
  const [errors, setErrors] = useState({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const { login } = useAuth()
  const navigate = useNavigate()

  function handleChange(event) {
    const { name, value } = event.target
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  function validate() {
    const nextErrors = {}

    if (!form.email.trim()) {
      nextErrors.email = 'Please enter your email address.'
    } else if (!isValidEmail(form.email)) {
      nextErrors.email = 'Please enter a valid email address.'
    }

    if (!form.password) {
      nextErrors.password = 'Please enter your password.'
    }

    if (!role) {
      nextErrors.role = 'Please select whether you are a Patient or Doctor.'
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
      // `role` here is only the requested login role — the backend
      // verifies it against the database and rejects the login on any
      // mismatch, so the redirect below uses the authenticated user's
      // actual role (from the post-login /auth/me refresh), never this
      // frontend value.
      const authenticatedUser = await login({
        email: form.email.trim(),
        password: form.password,
        role,
      })

      navigate(authenticatedUser?.role === 'doctor' ? '/doctor' : '/patient')
    } catch (error) {
      const message =
        error instanceof LoginError
          ? error.message
          : 'Unable to connect to MediSync. Please try again.'
      setSubmitError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthLayout message="Sign in to view your organized medical reports and stay connected with your care team.">
      <span className="eyebrow">Welcome</span>
      <h1 className="auth-heading">Welcome Back</h1>

      <form className="auth-form" onSubmit={handleSubmit} noValidate>
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
          placeholder="Enter your password"
          autoComplete="current-password"
        />

        <button type="button" className="auth-link-button">
          Forgot Password?
        </button>

        <RoleToggle label="Login as:" value={role} onChange={setRole} error={errors.role} />

        <button
          type="submit"
          className="btn btn-primary auth-submit"
          disabled={isSubmitting}
        >
          {isSubmitting ? 'Logging in...' : 'Login'}
        </button>

        {submitError && (
          <p className="auth-status auth-status-error" role="alert">
            {submitError}
          </p>
        )}
      </form>

      <p className="auth-switch">
        Don&apos;t have an account? <Link to="/signup">Create Account</Link>
      </p>
    </AuthLayout>
  )
}

export default LoginPage
