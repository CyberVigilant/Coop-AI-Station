# Co-op AI Station 🎓

An AI-powered platform that helps university students find co-op training opportunities in one place.

The platform automatically discovers new opportunities using a search API, and validates every submission through an AI pipeline before publishing — so students always see accurate and trustworthy listings.

---

## Features

- 🔍 **Automated Opportunity Discovery** — Serper API continuously searches for new co-op listings
- ✅ **AI Validation Pipeline** — Every opportunity is verified using Groq + VirusTotal before going live
- 🎓 **Student-Focused Interface** — Clean listings page built for university students
- 🛠 **Admin Dashboard** — Full control panel for managing opportunities and users
- 👤 **User Accounts** — Registration, login, and profile management

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django (Python) |
| Frontend | Bootstrap |
| Database | PostgreSQL |
| AI Validation | Groq API |
| Opportunity Search | Serper API |
| Security Check | VirusTotal API |
| Task Scheduling | Django Scheduler |

---

## Project Structure

```
├── main/                        # Core app
│   ├── views.py                 # Page logic
│   ├── models.py                # Database models
│   ├── forms.py                 # User forms
│   ├── admin_views.py           # Admin dashboard
│   ├── ai_validator.py          # AI validation logic
│   ├── link_validator.py        # URL + VirusTotal checks
│   ├── opportunity_observer.py  # Serper search integration
│   └── scheduler.py             # Automated scheduling
├── Conf/                        # Django settings & routing
├── static/                      # Static files
├── manage.py
├── Pipfile
└── .env.example                 # Environment variable template
```

---

## Getting Started

### Requirements

- Python 3.10+
- pipenv

```bash
pip install pipenv
```

### Setup

```bash
# 1. Clone the repo
git clone <repo-url>

# 2. Install dependencies
pipenv install

# 3. Activate environment
pipenv shell

# 4. Set up environment variables
cp .env.example .env
# Open .env and fill in your API keys

# 5. Run migrations
python manage.py migrate

# 6. Start the server
python manage.py runserver
```

Open in browser: `http://127.0.0.1:8000/`

---

## Environment Variables

Create a `.env` file based on `.env.example`:

```
GROQ_API_KEY=your_key_here
SERPER_API_KEY=your_key_here
VIRUSTOTAL_API_KEY=your_key_here
```

> ⚠️ Never commit your `.env` file. It is already excluded in `.gitignore`.
