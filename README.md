# Headhunt CRM (Simple Flask Version)

## Features
- Bulk CV upload (PDF/DOCX), up to 100 files per batch
- AI parsing with OpenAI `gpt-4o-mini` + regex fallback
- Candidate classification by industry + job function
- Candidate search (name/email/phone + filter)
- Editable candidate details after parse
- Client/Job/Placement management with fee auto-calc

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill OPENAI_API_KEY and FLASK_SECRET_KEY in .env
python app.py
```
Open: `http://127.0.0.1:5000`

## Security Notes
- Never hardcode API keys in source code.
- If key is leaked, revoke it immediately and create a new one.

## AI Parsing Fields
- name, email, phone, industry, job_function, years_of_experience, salary, date_of_birth
