import { useEffect, useState } from 'react'
import { useAuth } from '../../context/AuthContext.jsx'
import FormField from '../../components/auth/FormField.jsx'
import { ProfileError, fetchPatientProfile, updatePatientProfile } from '../../services/patientService.js'
import './ProfilePage.css'

function formatMemberSince(isoDate) {
  if (!isoDate) return ''
  try {
    return new Date(isoDate).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })
  } catch {
    return ''
  }
}

function ProfilePage() {
  const { updateUser } = useAuth()

  const [profile, setProfile] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  const [isEditing, setIsEditing] = useState(false)
  const [fullNameDraft, setFullNameDraft] = useState('')
  const [fieldError, setFieldError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [successMessage, setSuccessMessage] = useState('')

  useEffect(() => {
    let isMounted = true

    fetchPatientProfile()
      .then((data) => {
        if (!isMounted) return
        setProfile(data)
      })
      .catch((error) => {
        if (!isMounted) return
        setLoadError(
          error instanceof ProfileError
            ? error.message
            : 'Something went wrong loading your profile. Please try again.',
        )
      })
      .finally(() => {
        if (isMounted) setIsLoading(false)
      })

    return () => {
      isMounted = false
    }
  }, [])

  function startEditing() {
    setFullNameDraft(profile.full_name)
    setFieldError('')
    setSaveError('')
    setSuccessMessage('')
    setIsEditing(true)
  }

  function cancelEditing() {
    setIsEditing(false)
    setFieldError('')
    setSaveError('')
  }

  async function handleSave(event) {
    event.preventDefault()
    if (isSaving) return

    const trimmed = fullNameDraft.trim()
    if (!trimmed) {
      setFieldError('Please enter your name.')
      return
    }

    setFieldError('')
    setSaveError('')
    setIsSaving(true)

    try {
      const updated = await updatePatientProfile({ fullName: trimmed })
      setProfile(updated)
      updateUser(updated)
      setIsEditing(false)
      setSuccessMessage('Profile updated.')
    } catch (error) {
      setSaveError(
        error instanceof ProfileError
          ? error.message
          : 'Something went wrong saving your changes. Please try again.',
      )
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="patient-profile-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>Profile</h1>
      <p className="section-intro">Manage your personal information and account settings.</p>

      {isLoading && <p className="dashboard-card-empty">Loading your profile…</p>}

      {!isLoading && loadError && <p className="field-error profile-load-error">{loadError}</p>}

      {!isLoading && !loadError && profile && (
        <section className="dashboard-card profile-card">
          {successMessage && !isEditing && (
            <p className="profile-status profile-status-success">{successMessage}</p>
          )}

          {!isEditing ? (
            <>
              <div className="profile-field">
                <span className="profile-field-label">Full Name</span>
                <span className="profile-field-value">{profile.full_name}</span>
              </div>

              <div className="profile-field">
                <span className="profile-field-label">Email</span>
                <span className="profile-field-value">{profile.email}</span>
              </div>

              <div className="profile-field">
                <span className="profile-field-label">Role</span>
                <span className="profile-field-value profile-field-value-capitalize">
                  {profile.role}
                </span>
              </div>

              <div className="profile-field">
                <span className="profile-field-label">Member since</span>
                <span className="profile-field-value">{formatMemberSince(profile.created_at)}</span>
              </div>

              <button type="button" className="btn btn-secondary profile-edit-btn" onClick={startEditing}>
                Edit Profile
              </button>
            </>
          ) : (
            <form className="profile-edit-form" onSubmit={handleSave}>
              <FormField
                label="Full Name"
                name="fullName"
                value={fullNameDraft}
                onChange={(event) => setFullNameDraft(event.target.value)}
                error={fieldError}
                autoComplete="name"
              />

              <div className="profile-field profile-field-readonly">
                <span className="profile-field-label">Email</span>
                <span className="profile-field-value">{profile.email}</span>
              </div>

              <div className="profile-field profile-field-readonly">
                <span className="profile-field-label">Role</span>
                <span className="profile-field-value profile-field-value-capitalize">
                  {profile.role}
                </span>
              </div>

              <div className="profile-field profile-field-readonly">
                <span className="profile-field-label">Member since</span>
                <span className="profile-field-value">{formatMemberSince(profile.created_at)}</span>
              </div>

              {saveError && <p className="profile-status profile-status-error">{saveError}</p>}

              <div className="profile-edit-actions">
                <button type="submit" className="btn btn-primary" disabled={isSaving}>
                  {isSaving ? 'Saving…' : 'Save Changes'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={cancelEditing}
                  disabled={isSaving}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </section>
      )}
    </div>
  )
}

export default ProfilePage
