# Canopy Ticket System — QA Checklist

A step-by-step manual test plan. Values below (status names, SLA hours, limits,
error text) are pulled directly from the code, not approximate — use them to
judge pass/fail, not just "does it look right."

## 0. Why manual QA matters here

Automated test coverage is good for the core ticketing flows (dashboard
filtering, ticket CRUD, company CRUD, CSV export, portal auth/isolation, SLA
module) but **zero** for: outbound email notifications, password reset, and
inbound email-to-ticket processing. A regression in any of those three areas
will not be caught by CI — treat Suites 8-10 below as mandatory before every
release, not optional.

---

## 1. Environments — what you can test where

| Environment | How to run | Email (outbound) | SSO | Inbound email |
|---|---|---|---|---|
| **Local dev** (SQLite, `manage.py runserver`) | `SETUP.md` Quick Start | No (no Graph creds) — calls no-op/log quietly | No (no Azure creds) | No (`email-inbound` service not running) |
| **Docker Compose** (Postgres+Redis+nginx, all 5 services incl. `email-inbound`) | `docker compose up --build -d` | Yes, if `.env` has real `GRAPH_CLIENT_ID/SECRET/TENANT_ID` | Yes, if `.env` has real `AZURE_CLIENT_ID/SECRET` | Yes, polls every 5 min |
| **Production** | deployed stack | Yes | Yes | Yes |

Run Suites 1-7 and 11-13 in local dev. Suites 8-10 need the Docker stack with
real Microsoft Graph/Azure AD credentials in `.env` — local dev alone cannot
verify these.

---

## 2. Setup / preconditions

- [ ] `cd backend && python manage.py migrate` — confirm it completes with no
      pending-migration warnings (`python manage.py showmigrations` should show
      `[X]` on every line).
- [ ] `python manage.py seed_dev` — **always re-run this after pulling a DB
      that's more than a few weeks old.** Seeded demo passwords are reset by
      `set_password()` every run; an old DB can silently have a stale/different
      password hash even though the account exists (hit this exact issue
      2026-07-16 — login failed with "Please enter a correct email and
      password" despite the account being real and active).
- [ ] Confirm 4 demo users exist: `marc.gullo@canopytech.ca` /
      `tech2@canopytech.ca` (TECH), `jane@acmecorp.com` / `bob@globex.com`
      (CLIENT), all password `ChangeMe123!`.
- [ ] Confirm demo companies exist: Acme Corp, Globex Industries.

---

## 3. Authentication & access control

1. [ ] Go to `/auth/login/`. Log in as `marc.gullo@canopytech.ca` /
   `ChangeMe123!` → **expect** redirect into `/tech/` (tech queue).
2. [ ] Log out, log in as `jane@acmecorp.com` / `ChangeMe123!` → **expect**
   redirect into `/portal/` (client dashboard), not `/tech/`.
3. [ ] While logged in as `jane@acmecorp.com`, manually navigate to
   `/tech/` → **expect** access denied / redirect, not the tech queue.
4. [ ] Log in with a wrong password 6 times in a row from the same
   browser/IP → **expect** on the 6th attempt the exact message *"Too many
   login attempts. Please wait a minute and try again."* (rate limit is
   5/minute per-IP). Wait 60s, confirm a correct login succeeds again.
5. [ ] Log in with a valid email but wrong password once → **expect** the
   generic *"Please enter a correct email and password..."* error (not a
   different message that would leak whether the email exists).
6. [ ] Log out → **expect** redirect to `/auth/login/` and that the tech
   queue is no longer reachable without re-authenticating.
7. [ ] As `jane@acmecorp.com` (Acme Corp), confirm she cannot see or open
   any ticket belonging to Globex Industries (`bob@globex.com`'s company) —
   try guessing/incrementing a ticket URL if one is visible.

---

## 4. Tech portal — ticket queue

1. [ ] `/tech/` loads a paginated list of tickets.
2. [ ] Apply each filter individually (status, priority, assigned tech,
   company if available) and confirm the result set actually narrows
   correctly.
3. [ ] With a filter applied, page to page 2 (if enough tickets exist) →
   **expect** the filter stays applied (memory notes this was specifically
   built to persist across pages — regression-test it).
4. [ ] Clear filters → confirm the full queue returns.

---

## 5. Ticket lifecycle & detail

Status values: **Open, In Progress, Waiting on Client, Resolved, Closed**
(default on creation: Open). Priority values: **Low, Medium, High, Critical**
(default: Medium).

1. [ ] Create a new ticket from the tech side → **expect** status defaults to
   Open, priority defaults to Medium.
2. [ ] Change status through each value in the tech ticket detail view →
   **expect** each save persists and is reflected immediately.
3. [ ] Change priority → **expect** SLA countdown updates accordingly (see
   Suite 6).
4. [ ] Assign the ticket to a tech, then reassign to a different tech, then
   unassign → **expect** all three save correctly and the audit log records
   `TICKET_ASSIGN` each time (Suite 9).
5. [ ] Add an **internal note** (not visible to client) and an **external
   reply** (visible to client) on the same ticket → log in as the client and
   confirm only the external message is visible in their portal thread.
6. [ ] Set a ticket to **Waiting on Client**, then log in as the client and
   reply on that ticket → **expect** the status auto-flips back to **Open**
   (this happens in the client reply handler specifically when status ==
   Waiting on Client — confirm it does *not* fire from any other status).
7. [ ] Set category on a ticket, then clear it back to "none" → **expect**
   both save correctly (category is optional; clearing sets it to null, it
   does not error or delete the ticket).

---

## 6. SLA system

Response / resolve targets by priority:

| Priority | Response target | Resolve target |
|---|---|---|
| Critical | 1 hour | 4 hours |
| High | 4 hours | 8 hours |
| Medium | 8 hours | 24 hours |
| Low | 24 hours | 72 hours |

Warning threshold fires when **25% or less of the SLA window remains** (i.e.
75% of the time has elapsed) with no response/resolution yet. Breach means
the deadline has passed with nothing recorded.

1. [ ] Create a Critical ticket. Confirm the countdown display shows a
   sensible remaining time (~1h for response) and updates/recalculates on
   reload.
2. [ ] Manually backdate a ticket's `created_at` (via Django admin or shell)
   to put it at ~80% elapsed on a Medium ticket's response window (i.e. ~6.4h
   in) with no tech response yet → reload the ticket → **expect** it now
   shows the SLA **warning** state (≤25% remaining), not just "on track."
3. [ ] Backdate further past the full response window with still no
   response → **expect** the ticket shows **breached**, with text like
   "Breached Xh ago."
4. [ ] Respond to a breached ticket → **expect** it now shows as
   retroactively **missed** (not breached-and-counting), i.e. the SLA state
   freezes once a response/resolution is recorded.
5. [ ] Confirm the reports page's "Breached total" and "SLA compliance %"
   cards (Suite 11) reflect the tickets you just manipulated.

---

## 7. Time logging

1. [ ] On a ticket, log time via the time entry form. Field is **whole
   minutes only** (e.g. `30`, not `0.5`) — try entering `0` → **expect**
   rejection with *"Must be at least 1 minute."* Try a negative number and a
   decimal → confirm both are rejected or coerced sensibly, not silently
   accepted as-is.
2. [ ] Log `90` minutes → **expect** display converts to **"1h 30m"** in the
   UI (the raw minutes field is not what's shown to the user).
3. [ ] Confirm a `TIME_LOG` audit entry is created (Suite 9).
4. [ ] Confirm this ticket's logged time shows up correctly in the CSV time
   export (Suite 11).

---

## 8. Attachments

Limit: **10 MB per file**, enforced at the model level (not just JS/form —
try to actually confirm server-side enforcement, not just that the browser
blocks it). Allowed extensions: `.pdf .doc .docx .xls .xlsx .csv .txt .png
.jpg .jpeg .gif .webp .zip .7z .tar .gz .msg .eml .log`.

1. [ ] Upload a small PDF as a message attachment from the **tech** side →
   **expect** it appears on the message and in the ticket detail's
   attachments sidebar.
2. [ ] Upload a file from the **client** portal on the same ticket → confirm
   it appears correctly and that the tech-side sidebar updates (this is an
   HTMX out-of-band update — reload is not required to see it appear, watch
   for it updating live if you have the tech view open in another tab).
3. [ ] Attempt to upload an file **over 10 MB** → **expect** a clear
   rejection, not a silent failure or server error.
4. [ ] Attempt to upload a disallowed file type (e.g. `.exe`) → **expect**
   rejection.
5. [ ] Check whether an `ATTACHMENT_UPLOAD` audit log entry is created on
   successful upload (Suite 9) — **this needs explicit verification**, a
   code review found the action type defined but did not find a confirmed
   call site logging it. If it's missing, that's a real gap worth filing.

---

## 9. Audit log

Defined action types: `LOGIN, LOGIN_FAILED, LOGOUT, TICKET_CREATE,
TICKET_STATUS, TICKET_ASSIGN, MESSAGE_ADD, ATTACHMENT_UPLOAD, TIME_LOG,
USER_CREATE, USER_UPDATE, USER_DEACTIVATE, COMPANY_CREATE, COMPANY_UPDATE,
API_TICKET_CREATE`.

1. [ ] After working through Suites 3, 5, 7, and 8 above, check the audit
   log (tech UI or Django admin) and confirm you see entries for: LOGIN,
   LOGIN_FAILED, LOGOUT, TICKET_CREATE, TICKET_STATUS, TICKET_ASSIGN,
   MESSAGE_ADD, TIME_LOG.
2. [ ] Create/edit a user and a company → confirm `USER_CREATE`/`USER_UPDATE`
   and `COMPANY_CREATE`/`COMPANY_UPDATE` entries appear.
3. [ ] Deactivate a user (if there's a UI action for it) → check whether
   `USER_DEACTIVATE` actually fires — flagged as unverified in code review.
4. [ ] If the Chat Agent / external API integration is reachable in your
   test environment, create a ticket via that API path → confirm
   `API_TICKET_CREATE` fires (this is a distinct action from a
   tech/client-created ticket).
5. [ ] Run a bulk action (Suite 12) → confirm it logs as `TICKET_STATUS`
   with a detail string listing the affected ticket numbers (capped at 10
   in the log detail even if more were changed).

---

## 10. Companies & categories

**Companies** fields: name, slug (auto-generated), email domain (optional,
must be unique if set), phone, website, notes, active flag.

1. [ ] Create a company without an email domain → **expect** success (it's
   optional).
2. [ ] Create a second company and try to reuse an email domain already
   assigned to another company → **expect** a uniqueness validation error,
   not a silent duplicate.
3. [ ] Edit an existing company's fields → confirm changes persist and slug
   doesn't unexpectedly change on unrelated edits.
4. [ ] Deactivate a company → confirm the effect (e.g. does it hide from
   ticket-creation company pickers, block portal login for its users?
   confirm actual behavior, don't assume).

**Categories** fields: name (unique), color (one of 8 preset swatches,
default gray), active flag. Category is optional on a ticket.

5. [ ] Create a category with a name already in use → **expect** a
   uniqueness error.
6. [ ] Create/edit a category's color → confirm the chosen color actually
   renders on tickets using that category (ticket queue, ticket detail,
   reports category chart).
7. [ ] Deactivate a category → confirm it's removed from the picker for
   *new* tickets but doesn't break existing tickets still assigned to it.

---

## 11. Reports & CSV export

`/tech/reports/` — **no user-selectable date filters**, all windows are
fixed in code (this month / last 7 days / last 30 days) — don't go looking
for a date picker that isn't there.

1. [ ] Confirm summary cards show: **Total this month**, **Open total**,
   **Breached total**, **Avg resolution (30-day)**, **SLA compliance %
   (30-day)** — and that each number matches what you'd expect from manually
   counting tickets in that state.
2. [ ] Confirm 4 charts render: daily ticket volume (line), status
   breakdown (doughnut), category breakdown (bar), company breakdown (bar).
   Cross-check at least one chart's totals against the raw ticket list.
3. [ ] Trigger a state that should move the SLA compliance number (resolve
   a breached ticket, create a new breach) and confirm the report reflects
   it — no caching staleness.
4. [ ] CSV time export: download it, open it, confirm every time entry you
   logged in Suite 7 is present with correct minutes/hours and correct
   ticket/company attribution.

---

## 12. Bulk actions

Endpoint supports: **close, resolve, set status (any value), assign
(including unassign), set priority, set category (including clear)**. There
is **no bulk delete** — don't test for one, and flag it as a real gap if you
find a way to bulk-delete.

1. [ ] Select 3+ tickets via checkboxes in the queue, apply a bulk status
   change → confirm all selected tickets update, nothing outside the
   selection changes.
2. [ ] Bulk-assign to a tech, then bulk-unassign the same tickets → confirm
   both directions work.
3. [ ] Bulk set priority and bulk set category (including clearing category)
   → confirm both apply correctly across the whole selection.
4. [ ] Confirm the redirect after a bulk action returns you to where you
   were (the queue with your filters intact), not a blank/generic page.
5. [ ] Confirm this logs as a single `TICKET_STATUS` audit entry listing the
   ticket numbers (Suite 9.5), not one entry per ticket.

---

## 13. Client portal

1. [ ] Log in as a client, view the dashboard → confirm only that client's
   company's tickets appear.
2. [ ] Submit a new ticket → confirm it appears immediately in both the
   client's own list and the tech queue, with status **Open**.
3. [ ] Open a ticket thread and reply → confirm the message appears with the
   client correctly attributed as author, and (Suite 5.6) status auto-flips
   from Waiting on Client to Open if it was in that state.
4. [ ] Upload an attachment as a client (Suite 8.2 covers cross-checking
   this from the tech side).

---

## 14. Password reset & profile self-service *(needs Docker + real Graph creds)*

No automated test coverage exists here — be thorough.

1. [ ] From the login page, use "forgot password" → request a reset for a
   real test account's email.
2. [ ] Confirm an email actually arrives (via Graph) and that the link in it
   uses the correct `SITE_URL` (not a hardcoded localhost or wrong domain).
3. [ ] Click the link → confirm it lands on a working "set new password"
   form, and that an expired/already-used/malformed token shows a sensible
   error instead of a crash.
4. [ ] Set a new password meeting validation rules (min 12 characters — see
   `AUTH_PASSWORD_VALIDATORS`), confirm it saves, and log in with the new
   password (old password should no longer work).
5. [ ] Log in, go to profile settings, change name → confirm it saves and
   reflects in the header/audit log.
6. [ ] Change password from the profile page (not the forgot-password flow)
   → confirm the old password stops working and the new one works.

---

## 15. Outbound email notifications *(needs Docker + real Graph creds)*

No automated test coverage — verify manually every release. Expected
triggers: ticket created → assigned tech notified; client replies → tech
notified; tech replies → client notified; ticket assigned → tech notified;
status changed to Resolved/Closed/Waiting on Client → relevant party
notified.

1. [ ] Create a new ticket assigned to a tech → confirm that tech receives
   an email.
2. [ ] As the client, reply on an existing ticket → confirm the assigned
   tech receives an email.
3. [ ] As the tech, reply → confirm the client receives an email, and that
   internal notes do **not** trigger a client email (only external replies
   should).
4. [ ] Reassign a ticket to a different tech → confirm the newly-assigned
   tech gets notified (and confirm whether the previous tech does or
   doesn't — verify actual behavior).
5. [ ] Change status to Resolved, Closed, and Waiting on Client individually
   → confirm each produces the expected notification and that the email
   content/subject is correct for that specific event (all 6 templates
   should look distinct and correctly branded, not generic).
6. [ ] Confirm links inside every email actually resolve to the right
   ticket when clicked.

---

## 16. Inbound email-to-ticket *(needs Docker with `email-inbound` service + real Graph creds)*

No automated test coverage — this is the least-verified path in the whole
app. The poller runs every 5 minutes via the `email-inbound` Docker Compose
service (`docker-compose.yml`), not locally via `runserver`.

1. [ ] Confirm the `email-inbound` container is actually running:
   `docker compose ps` should show it as `Up`, not restarting/crashed.
2. [ ] Reply directly to a notification email (from Suite 15) from an
   external mail client, as if you were the client responding.
3. [ ] Wait up to 5 minutes, then confirm the reply was correctly pinned as
   a new message on the right ticket (not lost, not attached to the wrong
   ticket, not duplicated).
4. [ ] Send a reply with an attachment → confirm the attachment is captured
   too (or explicitly confirm it isn't, if that's not supported — verify
   actual behavior, don't assume).
5. [ ] Send an email that doesn't correspond to any known ticket (e.g. a
   fresh unsolicited email to the support address) → confirm it either
   creates a new ticket sensibly or is ignored cleanly — **not** that it
   crashes the poller or gets silently dropped in a confusing way.
6. [ ] Check container logs (`docker compose logs email-inbound`) after each
   of the above for errors, even if the end result looked correct in the UI.

---

## 17. Microsoft 365 SSO *(needs Docker + real Azure AD creds)*

1. [ ] From the login page, use "Sign in with Microsoft" as a user whose
   email domain matches an existing **active** company's `email_domain`.
   → **expect** successful login and correct auto-provisioning (role,
   company assignment).
2. [ ] Try SSO with an email domain that does **not** match any company's
   `email_domain` → **expect** the exact error *"No active account found
   for {email}. Contact your administrator to get access."* and that no
   account/session is created.
3. [ ] Try SSO with a domain matching a company that's marked **inactive**
   → confirm it's treated the same as "no match" (the lookup filters on
   `is_active=True`), not silently allowed through.
4. [ ] Confirm a `LOGIN` audit entry is created for successful SSO logins,
   same as password logins.
5. [ ] Confirm SSO login redirects to the correct portal (tech vs client)
   based on the auto-assigned role.

---

## 18. Security spot-checks

1. [ ] Confirm CSRF protection: try submitting a form (e.g. ticket reply)
   with a missing/invalid CSRF token via a raw HTTP request tool → expect
   403, not success.
2. [ ] In a production-like environment, confirm `SESSION_COOKIE_SECURE`
   and `CSRF_COOKIE_SECURE` are actually `True` (dev intentionally sets them
   `False` — don't flag that as a bug in local dev, but do flag it if prod
   is misconfigured).
3. [ ] Confirm the Django admin is reachable only at the configured
   `ADMIN_URL` (should be changed from the default `admin/` in real
   deployments per `.env.example`'s own comment) and is not accessible to
   non-superusers.
4. [ ] Re-confirm ticket/company data isolation between client accounts of
   different companies (cross-reference Suite 3.7) — this is the most
   damaging possible bug class in a multi-tenant portal.

---

## 19. Mobile / responsive

The "mobile friendly" commit is the most recent one in the repo — verify it
actually holds up.

1. [ ] Load the tech queue, a ticket detail page, and the client portal
   dashboard at a phone-width viewport (~375px) → confirm no horizontal
   scroll, no overlapping/clipped controls, and that the ticket
   filter/action controls are still usable (not just visible).
2. [ ] Confirm file upload and message reply both work on a touch/mobile
   viewport, not just desktop.

---

## 20. Filing what you find

For anything that fails, capture:
- Which suite/step number.
- Exact steps to reproduce (starting from a known state — fresh seed data
  if possible).
- What you expected (cite the spec value from this doc, e.g. "SLA warning
  should fire at 25% remaining") vs. what actually happened.
- Screenshot or copy of any error text shown.
- Whether it reproduces in local dev only, Docker only, or both — this
  narrows down whether it's a code bug vs. an environment/credentials issue.
