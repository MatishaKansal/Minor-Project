# Ledgerline audit frontend

A React audit workspace with Supabase Auth sign-in, role-aware audit screens, and authenticated FastAPI requests. Uploaded files are parsed and indexed in Supabase; retrieved chunks and source references remain available through document search.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

In another terminal, from the project root, start the API:

```powershell
pip install -r requirements.txt
uvicorn backend.api:app --reload
```

Open the local URL printed by Vite. The development server proxies `/api` to FastAPI at `127.0.0.1:8000`. To create a production build, run `npm run build`.

## Audit workbench

### Configure Supabase Auth

1. Keep `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` in the backend `.env`. The service-role key must never be copied into frontend files or Vite environment variables.
2. Create `frontend/.env` with the public project settings:

   ```dotenv
   VITE_SUPABASE_URL=https://your-project-ref.supabase.co
   VITE_SUPABASE_ANON_KEY=your-anon-or-publishable-key
   ```

3. Apply [`supabase/migrations/202611100001_audit_workbench.sql`](../supabase/migrations/202611100001_audit_workbench.sql) and then [`supabase/migrations/202611100002_supabase_auth_rbac.sql`](../supabase/migrations/202611100002_supabase_auth_rbac.sql) to the configured Supabase project.
4. Create/invite individual accounts in Supabase Auth. The database trigger creates each new account's `profiles` row with the `read_only_viewer` role and no business assignment.
5. Bootstrap the first administrator from the Supabase SQL editor by assigning an existing account to the project's business:

   ```sql
   update public.profiles
   set role = 'administrator',
       business_id = '<existing-business-uuid>'
   where email = '<administrator-email>';
   ```

6. Start the API and frontend using the commands above. Users sign in with their individual Supabase email/password. Sessions and refreshed JWT access tokens are managed by Supabase JS; each API call sends its token in `Authorization: Bearer <access-token>`.

The backend verifies each token with Supabase Auth, loads the user's role and business from `profiles`, and enforces authorization independently of frontend visibility. API roles are `administrator`, `auditor`, `reviewer`, and `read_only_viewer`. Administrators manage roles from **User access**. Auditors can create engagements, link invoices, run tests, and record resolution/review notes. Reviewers can approve engagement completion and make reviewer-only finding decisions. Read-only viewers can inspect records and query evidence but cannot upload or modify records.

The audit API uses the backend-only service-role client after authorization; the browser never receives that key. RLS policies scope direct authenticated reads to the profile's assigned business and deny direct writes to audit records.

The current data model supports duplicate invoice, invoice arithmetic, line-item tax, and invoice-period checks. Procedures requiring purchase orders, goods receipts, bank transactions, ledger records, or configured evidence requirements return **INSUFFICIENT EVIDENCE**. Finding severity is compared with engagement materiality (below threshold: Low; at or above: High); this is not a standalone risk score, and findings are never called fraud.
