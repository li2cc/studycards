# WikiCard Learning System

A Streamlit educational app that gathers presidential data from Wikipedia, stores it in SQLite, and provides study cards, quizzes, competitions, analytics, and leaderboards.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Features

- User registration and login
- Data harvesting from Wikipedia for US presidents
- Study card review mode
- Quiz mode with scoring and persistence
- Competition mode with speed-based scoring
- Leaderboard and analytics dashboard
- Admin tools for database management

## Deployment

This project is designed for Streamlit Community Cloud. Upload the project root and ensure `requirements.txt` is present.
