<div align="center">

# AFAQ — Event Discovery Platform

### Discover, plan, and enjoy events in Riyadh, all in one place.

[![Live Demo](https://img.shields.io/badge/🌐_Live_Demo-afaq--rawasi.pythonanywhere.com-0a7ea4?style=for-the-badge)](https://afaq-rawasi.pythonanywhere.com/)

![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-000000?logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-Database-4479A1?logo=mysql&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini_API-AI_Chatbot-8E75B2?logo=googlegemini&logoColor=white)
![Scrum](https://img.shields.io/badge/Process-Scrum-orange)

</div>

---

## 📖 About

**AFAQ** is a web-based platform that helps residents and visitors of **Riyadh, Saudi Arabia** find events that match their interests and schedule. Event information is usually scattered across many websites and social media accounts, so AFAQ gathers it in one place and adds the tools needed to plan attendance: favorites, a personal calendar, personalized recommendations, a smart schedule generator, and an AI chatbot.

> 🎓 Developed as the graduation project (**IT 497, Group 15**) at the Department of Information Technology, College of Computer and Information Sciences, **King Saud University**, using the **Scrum** framework.

🔗 **Try it live:** [https://afaq-rawasi.pythonanywhere.com/](https://afaq-rawasi.pythonanywhere.com/)

---

## 🚀 Features

| Feature | Description |
|---|---|
| 🔍 **Search & Filtering** | Find events by keyword, category, and date (today, tomorrow, this week, and more). |
| ⭐ **Ratings & Reviews** | Rate events and read what other users think. |
| ❤️ **Favorites** | Save events you are interested in. |
| 📅 **Personal Calendar** | Add events to your own calendar and keep track of your plans. |
| 🎯 **Personalized Recommendations** | Content-based recommendations (TF-IDF and cosine similarity) built from your favorites, ratings, and searches. |
| 🗓️ **Smart Schedule** | Generates a personalized visit plan for the dates you choose. |
| 🤖 **AI Chatbot** | A Gemini-powered assistant that answers questions about events and helps manage favorites and preferences. |
| 🔐 **Accounts & Verification** | Sign up, log in, and reset passwords with email verification codes. |
| 🛠️ **Admin Dashboard** | Admins can add, edit, and delete events to keep the data accurate. |

---

## 🧰 Technologies

| Category | Technologies |
|---|---|
| **Frontend** | HTML, CSS, JavaScript |
| **Backend** | Python, Flask, Flask-Login, Flask-Mail, SQLAlchemy |
| **Database** | MySQL (PyMySQL) |
| **Recommendation** | scikit-learn (TF-IDF, cosine similarity) |
| **AI Integration** | Gemini API (`google-genai`) |
| **Data Collection** | requests, pandas, Playwright (used by the data scripts) |
| **Hosting** | PythonAnywhere |

---

## 📁 Project Structure

```
AFAQ/
├── app.py                  # Application entry point
├── config.py               # App, database, and mail configuration
├── extensions.py           # Flask extensions (db, login, mail)
├── requirements.txt        # Python dependencies
├── models/                 # Database models (User, Event, Favorite, Rating, ...)
├── routes/                 # Flask blueprints (auth, events, favorites, calendar,
│                           #   smart schedule, chatbot, admin)
├── services/               # Business logic (recommendations, scheduling,
│                           #   chatbot, preferences, activity)
├── templates/              # HTML pages
├── static/                 # CSS, JavaScript, images
├── migrations/             # Database migrations
├── create_admins.py        # Creates the admin accounts
├── sync_riyadh_events.py   # Event data synchronization
└── *.py / *.json           # Data cleaning, image caching, and audit scripts
```

---

## ⚙️ Running AFAQ Locally

### Prerequisites

- **Python 3.9+**
- **MAMP** (or any MySQL server) with phpMyAdmin
- **Visual Studio Code** (or any editor)
- A **Gemini API key** (for the chatbot) and a **Gmail app password** (for email verification)

### Step 1 — Download the project

Clone the repository (or download it as a ZIP) and open the folder in Visual Studio Code:

```bash
git clone <repository-url>
cd <repository-folder>
```

### Step 2 — Start MAMP

Open **MAMP** and start the servers.

### Step 3 — Set up the database

1. Open **phpMyAdmin** through MAMP.
2. Create a MySQL database named `afaq_db`.
3. Import the **`afaq_db.sql`** file into it.

> ⚠️ The database connection is defined in `config.py` (`root:root@localhost:3306/afaq_db`). If your MAMP MySQL runs on a different port (MAMP often uses `8889`) or uses different credentials, update that line.

### Step 4 — Install the dependencies

Open the terminal in Visual Studio Code and run:

```bash
python -m pip install -r requirements.txt
```

### Step 5 — Create the `.env` file

Create a file named `.env` in the project root with the following values:

```env
SECRET_KEY=your-secret-key
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-gmail-app-password
GEMINI_API_KEY=your-gemini-api-key
```

> 🔒 Never commit the `.env` file. It contains private keys.

### Step 6 — Create the admin accounts (optional)

```bash
python create_admins.py
```

Change the default admin passwords after the first login.

### Step 7 — Run the application

```bash
python app.py
```

### Step 8 — Open the website

Open the local URL displayed in the terminal (usually `http://127.0.0.1:5000`) to access AFAQ.

---

## 📚 Documentation

- **User Guide:** `15_AFAQ_UserGuide.pdf`
- **Admin Guide:** `15_AFAQ_AdminGuide.pdf`
- **Release-2 Report:** `15_AFAQ_Release-2.pdf`

---

## 👩‍💻 Team

| Name | ID |
|---|---|
| Rawasi Almutairi | 444200484 |
| Numyir Almoqhim | 444200419 |
| Razan Almutairi | 444200495 |
| Layan Murayshid | 444201057 |

**Supervisor (Product Owner & Scrum Master):** Dr. Entesar Almosallam

See the [AUTHORS](AUTHORS) file for contact details.

---

<div align="center">

King Saud University · Fall 2026

</div>
