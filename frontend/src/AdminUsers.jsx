import { useCallback, useEffect, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'

const roles = ['superadmin', 'administrator']
const roleLabels = {
  superadmin: 'Superadmin',
  administrator: 'Administrator',
}

export default function AdminUsers() {
  const { apiFetch } = useAuth()
  const [profiles, setProfiles] = useState([])
  const [businesses, setBusinesses] = useState([])
  const [loading, setLoading] = useState(true)
  const [busyUser, setBusyUser] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadProfiles = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [response, businessResponse] = await Promise.all([
        apiFetch('/api/admin/profiles'),
        apiFetch('/api/admin/businesses'),
      ])
      const body = await response.json().catch(() => ({}))
      const businessBody = await businessResponse.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not load profiles (${response.status}).`)
      if (!businessResponse.ok) throw new Error(businessBody.detail || `Could not load businesses (${businessResponse.status}).`)
      setProfiles(body.profiles || [])
      setBusinesses(businessBody.businesses || [])
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => { loadProfiles() }, [loadProfiles])

  async function updateProfile(userId, role, businessId) {
    if (!businessId) {
      setError('Assign a business before changing the role.')
      return
    }
    setBusyUser(userId)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/admin/profiles/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role, business_id: businessId }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not update role (${response.status}).`)
      setProfiles((current) => current.map((profile) => profile.user_id === userId ? body.profile : profile))
      setNotice(`Access updated for ${body.profile.email || userId}.`)
    } catch (updateError) {
      setError(updateError.message)
    } finally {
      setBusyUser('')
    }
  }

  return (
    <section className="audit-panel admin-users-panel">
      <div className="audit-section-heading"><div><h2>User access</h2><p>Assign a business and role to each account. Superadmins can manage access across all businesses.</p></div><button className="audit-button secondary" onClick={loadProfiles}>Refresh</button></div>
      {error && <div className="audit-alert error" role="alert">{error}</div>}
      {notice && <div className="audit-alert success" role="status">{notice}</div>}
      {loading ? <div className="audit-empty small-empty">Loading user profiles…</div> : profiles.length === 0 ? <div className="audit-empty small-empty">No profiles found. Invite users in Supabase Auth first.</div> : (
        <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>User</th><th>Business assignment</th><th>Role</th><th>Joined</th></tr></thead><tbody>
          {profiles.map((profile) => <tr key={profile.user_id}>
            <td>{profile.email || 'Email unavailable'}<small>{profile.user_id}</small></td>
            <td><select aria-label={`Business for ${profile.email || profile.user_id}`} value={profile.business_id || ''} disabled={busyUser === profile.user_id} onChange={(event) => updateProfile(profile.user_id, profile.role, event.target.value)}>
              <option value="" disabled>Not assigned</option>
              {businesses.map((business) => <option value={business.business_id} key={business.business_id}>{business.business_name || business.business_id}</option>)}
            </select></td>
            <td><select aria-label={`Role for ${profile.email || profile.user_id}`} value={profile.role} disabled={busyUser === profile.user_id} onChange={(event) => updateProfile(profile.user_id, event.target.value, profile.business_id)}>
              {roles.map((role) => <option value={role} key={role}>{roleLabels[role]}</option>)}
            </select></td>
            <td>{profile.created_at ? new Date(profile.created_at).toLocaleDateString() : '—'}</td>
          </tr>)}
        </tbody></table></div>
      )}
    </section>
  )
}
