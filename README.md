# Smart Follow-up Assistant

Smart Follow-up Assistant is a Django-based follow-up management system for clinical research and longitudinal outpatient tracking. It supports patient master records, repeated treatment episodes, structured follow-up visits, role-based permissions, CSV export, and an optional AI side panel for patient-specific follow-up assistance.

This repository is the open-source package. It does not ship with private deployment materials, local databases, personal contact information, or production API keys.

## Features

- Patient master records with auto-generated patient IDs
- Multiple treatment episodes for the same patient
- Multiple follow-up records under each treatment episode
- Planned next follow-up date with manual override
- Card and table views with filtering, sorting, and pagination
- Aggregated data export:
  - patient basics CSV
  - treatment records CSV
  - follow-up records CSV
- Role-based access:
  - `root`
  - `admin`
  - `normal`
- Time-window-based edit/delete restrictions for non-root users
- Optional AI assistant drawer on the patient detail page

## Stack

- Python 3.11+
- Django 5.2
- SQLite by default
- Gunicorn + Nginx for Linux deployment

## Project Structure

- `config/`: Django project settings and URLs
- `followup/`: core business logic, models, forms, permissions, views
- `templates/`: Django templates
- `static/`: CSS assets
- `deploy_aliyun.sh`: one-command Linux deployment script

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env.local` and fill in the values you need.
4. Run migrations.
5. Start the development server.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local
python manage.py migrate
python manage.py runserver
```

Default local URL:

```text
http://127.0.0.1:8000/
```

## Environment Variables

Key settings are loaded from `.env.local` and `.env.server`.

- `FOLLOWUP_APP_NAME`
- `FOLLOWUP_APP_SUBTITLE`
- `FOLLOWUP_APP_COPYRIGHT`
- `FOLLOWUP_APP_NOTICE`
- `FOLLOWUP_SUPPORT_EMAIL`
- `FOLLOWUP_DEBUG`
- `DJANGO_SECRET_KEY`
- `FOLLOWUP_DB_PATH`
- `FOLLOWUP_ALLOWED_HOSTS`
- `FOLLOWUP_CSRF_TRUSTED_ORIGINS`
- `AI_PROVIDER`
- `AI_API_KEY`
- `AI_MODEL`
- `AI_BASE_URL`
- `AI_USE_ENV_PROXY`

## Root Account

This project does not hardcode a public default root password.

To create or reset the root account manually:

```bash
python manage.py ensure_root_account --username root --password "YourStrongPasswordHere"
```

If you run the Linux deployment script without `FOLLOWUP_ROOT_PASSWORD`, it generates a random root password and prints it once during deployment.

## AI Integration

The optional AI drawer supports text-only models. The current repository is compatible with:

- Aliyun DashScope OpenAI-compatible chat endpoints
- Zhipu Open Platform compatible chat endpoints

By default, the AI patient context excludes direct identifiers such as patient name, phone number, and address.

## Linux Deployment

The repository includes `deploy_aliyun.sh` for Ubuntu/Debian servers.

```bash
bash deploy_aliyun.sh
```

The script will:

- install system dependencies
- create a virtual environment
- install Python requirements
- prepare `.env.server`
- run migrations
- collect static files
- ensure a root account exists
- configure Gunicorn
- configure Nginx

After deployment, the app is served through Nginx on port `80`.

## Testing

Run checks and tests with:

```bash
python manage.py check
python manage.py test followup.tests
```

## Privacy and Open-source Notes

- No production API key is included in this repository.
- No personal contact email is included by default.
- No deployment database is included.
- Generated release documents and filing materials are intentionally excluded from version control.

## License

Released under the [MIT License](LICENSE).
