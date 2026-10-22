# AFAQ – Event Discovery Platform

A web-based platform for discovering and organizing events in Riyadh, Saudi Arabia.

---

## 1. Project Title

**AFAQ – Event Discovery Platform**

AFAQ is designed to help residents and visitors discover and organize events in Riyadh.

## 2. Product Goal

AFAQ aims to simplify event discovery through the following features:

- Event search and filtering
- Favorites and personal calendars
- Personalized event recommendations
- AI-powered chatbot assistance

## 3. Technologies

| Category | Technologies |
|----------|--------------|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, Flask |
| Database | MySQL |
| AI Integration | Gemini API |



4. Launching the Project

Follow these steps to run AFAQ locally:

Step 1: Download the Project

Download the project from GitHub and open it in Visual Studio Code.

Step 2: Start MAMP

Open MAMP and start the local servers to enable MySQL.

Step 3: Set Up the Database

1. Open phpMyAdmin through MAMP.
2. Create a MySQL database named afaq_db.
3. Import the afaq_db.sql file into the database.

Step 4: Install Dependencies

Open the terminal in Visual Studio Code and run:

pip install -r requirements.txt

If needed, install python-dotenv:

pip install python-dotenv

Step 5: Configure the Project

Configure the database connection and required environment variables according to the project settings.

Step 6: Run the Application

Run the Flask application using:

python app.py

Step 7: Open the Website

Open the local URL displayed in the terminal to access AFAQ.
