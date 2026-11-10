import { useCallback, useEffect, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'

const roles = ['administrator', 'auditor', 'reviewer', 'read_only_viewer']
const roleLabels = {
  administrator: 'Administrator',
  auditor: 'Auditor',
  reviewer: 'Reviewer',
  read_only_viewer: 'Read-only Viewer',
}

export default function AdminUsers() {
  const { apiFetch } = useAuth()
  const [profiles, setProfiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [busyUser, setBusyUser] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadProfiles = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiFetch('/api/admin/profiles')
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not load profiles (${response.status}).`)
      setProfiles(body.profiles || [])
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => { loadProfiles() }, [loadProfiles])

  async function setRole(userId, role) {
    setBusyUser(userId)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(`/api/admin/profiles/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not update role (${response.status}).`)
      setProfiles((current) => current.map((profile) => profile.user_id === userId ? body.profile : profile))
      setNotice(`Role updated for ${body.profile.email || userId}.`)
    } catch (updateError) {
      setError(updateError.message)
      await loadProfiles()
    } finally {
      setBusyUser('')
    }
  }

  return (
    <section className="audit-panel admin-users-panel">
      <div className="audit-section-heading"><div><h2>User access</h2><p>Roles are stored in Supabase profiles. Unassigned accounts are associated with your business when a role is saved.</p></div><button className="audit-button secondary" onClick={loadProfiles}>Refresh</button></div>
      {error && <div className="audit-alert error" role="alert">{error}</div>}
      {notice && <div className="audit-alert success" role="status">{notice}</div>}
      {loading ? <div className="audit-empty small-empty">Loading user profiles…</div> : profiles.length === 0 ? <div className="audit-empty small-empty">No profiles found. Invite users in Supabase Auth first.</div> : (
        <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>User</th><th>Business assignment</th><th>Role</th><th>Joined</th></tr></thead><tbody>
          {profiles.map((profile) => <tr key={profile.user_id}>
            <td>{profile.email || 'Email unavailable'}<small>{profile.user_id}</small></td>
            <td>{profile.business_id ? 'Assigned to this business' : 'Not assigned'}</td>
            <td><select aria-label={`Role for ${profile.email || profile.user_id}`} value={profile.role} disabled={busyUser === profile.user_id} onChange={(event) => setRole(profile.user_id, event.target.value)}>
              {roles.map((role) => <option value={role} key={role}>{roleLabels[role]}</option>)}
            </select></td>
            <td>{profile.created_at ? new Date(profile.created_at).toLocaleDateString() : '—'}</td>
          </tr>)}
        </tbody></table></div>
      )}
    </section>
  )
}
