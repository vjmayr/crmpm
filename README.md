# CRMPM

Lightweight CRM & Project Management tool. See `ARCHITECTURE.md` for the design blueprint and `CLAUDE.md` for standing orders.

## Quickstart

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SECRET_KEY, DATABASE_URL for production
flask db upgrade
flask create-user      # prompts for email/password
flask run
```
