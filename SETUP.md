# Canopy Ticket System — Setup Guide

## Quick Start (local dev, no Docker)

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Run migrations (uses SQLite in dev mode)
python manage.py migrate

# Seed demo users and tickets
python manage.py seed_dev

# Start the dev server
python manage.py runserver
```

Open http://localhost:8000 and sign in with:
- **Tech portal:** marc.gullo@canopytech.ca / ChangeMe123!
- **Client portal:** jane@acmecorp.com / ChangeMe123!

---

## Docker (production-style)

1. Copy `.env.example` to `.env` in the project root and fill in values.
2. Build and start:
```bash
docker compose up --build -d
```
3. Run migrations and seed:
```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_dev
```

Access on port 8080 (behind nginx), then point your Cloudflare tunnel to `localhost:8080`.

---

## Create a superuser (skip seed)
```bash
python manage.py createsuperuser
```
Admin panel: http://localhost:8000/admin/

---

## Project layout

```
backend/
  apps/
    accounts/   — User model (tech vs client), auth views
    companies/  — Company (multi-tenant)
    tickets/    — Ticket, Message, TimeEntry; tech + client views
  config/
    settings/   — base / dev / prod
  templates/
    accounts/   — login page
    tech/       — tech portal layout + pages
    portal/     — client portal layout + pages
```

## Portals
- `/tech/` — tech queue, ticket detail, time logging, assignment
- `/portal/` — client ticket list, submit ticket, reply to thread
- `/admin/` — Django admin (add companies, users, etc.)
