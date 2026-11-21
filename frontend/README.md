# Ledgerline frontend

A React financial workspace with Supabase Auth sign-in and authenticated FastAPI requests. Company Admins see a guided business-owner interface; Super Admins use a separate platform management dashboard.

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

## Company workspace

Use **Overview** for the next step and company totals. **My Documents** lists the latest 100 saved documents. Select files and confirm the upload to store and process each one independently, without creating an assistant conversation. Download the original and choose **Review extraction** to see extracted fields, source text and suspected duplicate invoices. Duplicate matches are suggestions, not automatic deletions. **Ask AI** opens a draft question; it does not submit it. Processed documents are indexed for search when a question is submitted, not during document upload. Chat messages currently live only in page memory and are lost on refresh.

Uploads require the existing `source_file.processing_status` constraint to accept `pending`, `processing`, `processed`, and `failed`; inspect the deployed constraint before enabling this workflow. If a server stops during processing, an operator must verify that no request is still running before changing that file's status for retry. The upload workflow cannot yet persist bank-statement transactions.

**Transactions** shows up to 500 extracted invoice records, not bank entries. Matching only uses records available in the current API server session, compares amounts, and requires human review; it is not a complete bank reconciliation.

In **Financial Checks**, create a review, choose invoice records, run checks, and review issues against their supporting documents. Checks lacking evidence are not counted as completed or passed. The overview's completed-check count includes definite results from all saved runs, including repeat runs. Advanced details retain the technical audit workspace. **Reports** provides a printable summary of a selected run; choose Print / Save PDF in the browser. The report shows at most 5,000 results and 500 issues. **Company Settings** contains company information and existing connector controls.

## Authentication setup

### Configure Supabase Auth

1. Keep `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` in the backend `.env`. The service-role key must never be copied into frontend files or Vite environment variables.
2. Create `frontend/.env` with the public project settings:

   ```dotenv
   VITE_SUPABASE_URL=https://your-project-ref.supabase.co
   VITE_SUPABASE_ANON_KEY=your-anon-or-publishable-key
   ```

3. Back up the database and review the existing schema, roles, signup triggers, storage policies and security-definer functions. The existing platform schema and RLS policies are prerequisites; the previous migration files are not present in this workspace. Apply [`20261121000000_username_company_accounts.sql`](../supabase/migrations/20261121000000_username_company_accounts.sql) in the Supabase SQL editor, or configure `SUPABASE_DB_URL` and `SUPABASE_DB_CA_FILE` and run `python scripts/apply_migrations.py`. The migration requires at most one administrator profile per company.
4. Ensure an existing trusted profile has the `superadmin` role. Sign in with its existing email and password, then choose a username in the platform dashboard. In **Companies**, verify the GSTIN of each existing company against its registration records; invoice-derived GSTINs are not trusted. The Super Admin can then register a company and set its Company Admin username and password. Share credentials securely. No invitation email is sent.
5. Start the API and frontend using the commands above. Company Admins sign in with their username and password. Existing accounts can continue to use email and password. Sessions and refreshed JWT access tokens are managed by Supabase JS; authenticated API calls send their token in `Authorization: Bearer <access-token>`.

The backend uses private, non-deliverable Supabase Auth email identifiers for new username accounts. Do not use email recovery for those accounts. If provisioning fails, the company and username reservation remain visible in Companies and account setup can be retried. If the API process is interrupted while a setup is marked in progress, an operator must inspect the Auth account and reservation before clearing the claim; do not delete or reassign an account blindly.

After confirming no provisioning request for that company is still running, an operator can clear a stuck claim in the SQL editor with the company's exact ID:

```sql
update public.company_account_reservations
set status = 'failed', attempt_id = null
where business_id = '<company-uuid>' and status = 'creating';
```

Then retry from the company details page. The retry verifies ownership of any previously created Auth account.

Use HTTPS in production and protect `/api/auth/login` with rate limiting at the reverse proxy. Supabase also applies its own authentication limits, but requests reach it from the backend rather than directly from each browser.

The backend verifies each token with Supabase Auth and enforces two roles. Super Admins use the platform dashboard for registration, status changes and aggregate monitoring; they cannot access the financial audit APIs. Company Admins work only in their assigned active company, including uploads, audits and connector authorization, and cannot manage companies or users.

The API uses the backend-only service-role client after authorization; the browser never receives that key. The migrations add restrictive policies to the listed tenant tables, including for suspended companies, but cannot account for unknown existing storage policies, triggers or security-definer functions. Review those separately and test with real authenticated accounts before deployment. Connector sessions and recent in-memory audit results are isolated by business but are not persistent and are not shared between API worker processes; restarting the API requires connectors to be authorized again.

The current data model supports duplicate invoice, invoice arithmetic, line-item tax, and invoice-period checks. Procedures requiring purchase orders, goods receipts, bank transactions, ledger records, or configured evidence requirements return **INSUFFICIENT EVIDENCE**. Finding severity is compared with engagement materiality (below threshold: Low; at or above: High); this is not a standalone risk score, and findings are never called fraud.
