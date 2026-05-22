# Coop Station 🚀
University Co-op Opportunity Platform built with Django.

This guide explains how to run the project locally on **Windows** and **macOS** after downloading the project as a ZIP file or cloning it.

---

## 📦 Requirements

Before starting, make sure you have:

- Python 3.10 or higher  
- pip (comes with Python)  
- pipenv  

Check Python version:

```bash
python --version
```

or

```bash
python3 --version
```

---

## 🔧 Install pipenv (One Time)

### Windows

```bash
pip install pipenv
```

### macOS

```bash
python3 -m pip install --user pipenv
```

Verify installation:

```bash
pipenv --version
```

---

## 📥 Download Project

### Option 1 (ZIP)

1. Click **Code → Download ZIP**
2. Extract the folder
3. Open the folder in VS Code

### Option 2 (Git)

```bash
git clone <repo-url>
```

---

## ⚙️ Setup Project

Open terminal inside project folder.

Install dependencies:

```bash
pipenv install
```

Activate environment:

```bash
pipenv shell
```

---

## ▶️ Run Server

```bash
python manage.py runserver
```

or

```bash
pipenv run python manage.py runserver
```

---

## 🌐 Open in Browser

```
http://127.0.0.1:8000/
```

---

## 🗃 Database Setup (First Time Only)

```bash
python manage.py migrate
```

(Optional) Create admin user:

```bash
python manage.py createsuperuser
```

---

## 🔑 Environment Variables

This project requires API keys that are NOT included in the repo.

### Step 1 — Create your `.env` file

In the project root (same folder as `manage.py`), create a file called `.env`.

**macOS/Linux:**
```bash
cp .env.example .env
```

**Windows (Command Prompt):**
```bash
copy .env.example .env
```

### Step 2 — Add the keys

Open `.env` in any text editor and fill in the values you received:

```
GROQ_API_KEY=paste_your_groq_key_here
SERPER_API_KEY=paste_your_serper_key_here
```

For example, if your Groq key is `gsk_abc123`, the file should look like:

```
GROQ_API_KEY=gsk_abc123
SERPER_API_KEY=1234abcd...
```

No quotes, no spaces around the `=` sign.

### Step 3 — Restart the server

If the server is already running, stop it (`Ctrl+C`) and start it again:

```bash
python manage.py runserver
```

The keys are loaded automatically on startup.

> ⚠️ Do **not** commit your `.env` file — it is already in `.gitignore`.

---

## 🛑 Common Errors

### No module named django

```bash
pipenv shell
```

### python not found

```bash
python3 manage.py runserver
```

---

## 🚫 Do NOT Commit These Files

```
__pycache__/
db.sqlite3
.env
```

---

## 👥 Team Workflow

Each developer:

- Downloads project  
- Runs `pipenv install`  
- Runs server locally  

Virtual environments are NOT shared.

---

## 📫 Support

If setup fails, send a screenshot of the error in the group chat.

Happy coding 🎉
