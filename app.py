import json
import random
import re
import sqlite3
import time
from datetime import datetime
from typing import List
from urllib.parse import urljoin

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

DB_PATH = "wikicard.db"
HEADERS = {"User-Agent": "WikiCard Learning System/1.0 (Educational App)"}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS Users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            date_created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS Profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            profile_name TEXT,
            bio TEXT,
            date_created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES Users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS Cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            image_url TEXT,
            category TEXT NOT NULL,
            hint TEXT,
            answer TEXT,
            details TEXT,
            source_url TEXT,
            highlights TEXT,
            presidency_order INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS QuizResults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            questions_attempted INTEGER NOT NULL,
            correct_answers INTEGER NOT NULL,
            date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES Users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS CompetitionResults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            correct_answers INTEGER NOT NULL,
            completion_time REAL NOT NULL,
            date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES Users(id)
        )
        """
    )
    conn.commit()
    conn.close()


def fetch_html(url: str):
    response = requests.get(url, timeout=30, headers=HEADERS)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_text(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split())


def normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://en.wikipedia.org" + url
    return url


def build_wikipedia_article_url(name: str) -> str:
    if not name:
        return ""
    article_name = name.strip().replace(" ", "_")
    return f"https://en.wikipedia.org/wiki/{article_name}"


def extract_first_paragraph(soup: BeautifulSoup) -> str:
    paragraphs = soup.select("p")
    for paragraph in paragraphs:
        text = clean_text(paragraph.get_text(" ", strip=True))
        if not text or len(text) < 40:
            continue
        if text.startswith(("This article", "For other uses", "The following")):
            continue
        if "may refer to" in text.lower():
            continue
        if paragraph.find("sup") is not None:
            text = re.sub(r"\[\d+\]", "", text)
        if len(text) > 40 and not text.startswith("This article"):
            return text
    return "No summary available."


def extract_president_highlights(soup: BeautifulSoup):
    infobox = soup.select_one("table.infobox") or soup.select_one("table.infobox.vcard")
    highlights = []

    if infobox:
        for row in infobox.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue
            label = clean_text(th.get_text(" ", strip=True)).lower()
            value = clean_text(td.get_text(" ", strip=True))
            if not value or len(value) > 180:
                continue
            if any(keyword in label for keyword in [
                "party", "term", "in office", "predecessor", "successor", "office",
                "vice president", "born", "died", "spouse", "election", "resting place"
            ]):
                formatted = value if label in {"party", "term"} else f"{label.title()}: {value}"
                if formatted not in highlights:
                    highlights.append(formatted)

    if not highlights:
        summary = extract_first_paragraph(soup)
        if summary:
            highlights.append(summary)

    cleaned = []
    for item in highlights[:5]:
        text = clean_text(str(item))
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:5]


def ensure_cards_schema():
    conn = get_connection()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(Cards)").fetchall()]
    if "source_url" not in columns:
        conn.execute("ALTER TABLE Cards ADD COLUMN source_url TEXT")
    if "highlights" not in columns:
        conn.execute("ALTER TABLE Cards ADD COLUMN highlights TEXT")
    if "presidency_order" not in columns:
        conn.execute("ALTER TABLE Cards ADD COLUMN presidency_order INTEGER")

    rows = conn.execute(
        "SELECT id, name, details, source_url, highlights, presidency_order FROM Cards WHERE category = 'US Presidents'"
    ).fetchall()
    for row in rows:
        name = row["name"]
        article_url = (row["source_url"] or build_wikipedia_article_url(name)).strip()
        if not row["source_url"]:
            conn.execute(
                "UPDATE Cards SET source_url = ? WHERE id = ?",
                (article_url, row["id"]),
            )
        if not row["highlights"]:
            conn.execute(
                "UPDATE Cards SET highlights = ? WHERE id = ?",
                (json.dumps([name], ensure_ascii=False), row["id"]),
            )

    conn.commit()
    conn.close()


def extract_infobox_value(soup: BeautifulSoup, label_keywords):
    infobox = soup.select_one("table.infobox") or soup.select_one("table.infobox.vcard")
    if not infobox:
        return ""
    for row in infobox.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if th and td:
            label = clean_text(th.get_text(" ", strip=True)).lower()
            if any(keyword in label for keyword in label_keywords):
                return clean_text(td.get_text(" ", strip=True))
    return ""


def fetch_president_profile(url: str):
    soup = fetch_html(url)
    infobox = soup.select_one("table.infobox") or soup.select_one("table.infobox.vcard")

    image_url = ""
    if infobox:
        img = infobox.find("img")
        if img and img.get("src"):
            image_url = normalize_url(img["src"])

    h1_tag = soup.select_one("h1")
    if h1_tag:
        name = clean_text(h1_tag.get_text(" ", strip=True))
    elif soup.title:
        name = clean_text(soup.title.get_text(" ", strip=True))
    else:
        name = ""
    if not name:
        h1_tag = soup.find("h1")
        name = clean_text(h1_tag.get_text(" ", strip=True)) if h1_tag else ""

    party = extract_infobox_value(soup, ["party", "political party", "affiliation"])
    years_in_office = extract_infobox_value(soup, ["in office", "term", "tenure", "presidency"])
    summary = extract_first_paragraph(soup)
    highlights = extract_president_highlights(soup)

    if not party:
        party = "Unknown"
    if not years_in_office:
        years_in_office = "Unknown"
    if not image_url:
        image_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/USA_flag_50_stars.svg/512px-USA_flag_50_stars.svg.png"
    if not highlights:
        highlights = [summary][:5]

    return {
        "name": name,
        "image_url": image_url,
        "party": party,
        "years": years_in_office,
        "summary": summary,
        "source_url": url,
        "highlights": highlights,
    }


init_db()
ensure_cards_schema()


def ensure_initial_cards_loaded() -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM Cards WHERE category = 'US Presidents'").fetchone()[0]
    conn.close()

    if count > 0:
        return count

    try:
        imported = fetch_president_cards(force_refresh=True, topic="US Presidents")
        return len(imported)
    except Exception:
        return 0


def card_exists_by_name(name: str) -> bool:
    conn = get_connection()
    result = conn.execute("SELECT 1 FROM Cards WHERE lower(name) = lower(?) LIMIT 1", (name,)).fetchone()
    conn.close()
    return result is not None


def refresh_president_metadata_from_list():
    list_url = "https://en.wikipedia.org/wiki/List_of_presidents_of_the_United_States"
    soup = fetch_html(list_url)
    conn = get_connection()
    seen = set()
    order = 0

    for row in soup.select("table.wikitable tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        name_cell = cells[1] if len(cells) > 1 else cells[0]
        link = name_cell.select_one("a")
        if not link:
            continue
        name = clean_text(link.get_text(" ", strip=True))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        order += 1
        office_range = clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else "Unknown"
        card_row = conn.execute(
            "SELECT id, details, hint FROM Cards WHERE category = 'US Presidents' AND lower(name) = lower(?) LIMIT 1",
            (name,),
        ).fetchone()
        if not card_row:
            continue
        parts = [part.strip() for part in str(card_row["details"] or "").split("|") if part.strip()]
        if len(parts) >= 2:
            party = parts[1].strip()
            summary = parts[-1].strip() if len(parts) >= 3 else "No summary available."
        else:
            party = card_row["hint"] or "Unknown"
            summary = "No summary available."
        updated_details = f"{office_range} | {party} | {summary}"
        conn.execute(
            "UPDATE Cards SET presidency_order = ?, details = ? WHERE id = ?",
            (order, updated_details, card_row["id"]),
        )
    conn.commit()
    conn.close()


def fetch_president_cards(force_refresh: bool = False, topic: str = "US Presidents") -> List[dict]:
    if topic != "US Presidents":
        return []

    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM Cards WHERE category = 'US Presidents'").fetchone()[0]
    conn.close()

    if count > 0 and not force_refresh:
        refresh_president_metadata_from_list()
        return []

    if force_refresh:
        conn = get_connection()
        conn.execute("DELETE FROM Cards WHERE category = 'US Presidents'")
        conn.commit()
        conn.close()

    list_url = "https://en.wikipedia.org/wiki/List_of_presidents_of_the_United_States"
    soup = fetch_html(list_url)
    collected = []
    seen_names = set()
    presidency_order = 0

    for row in soup.select("table.wikitable tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        name_cell = cells[1] if len(cells) > 1 else cells[0]
        primary_text = clean_text(name_cell.get_text(" ", strip=True))
        if not primary_text or primary_text[0].isdigit():
            continue
        primary_lower = primary_text.lower()
        if primary_lower.startswith(("vacant", "national", "election", "vice")):
            continue
        if "presidential election" in primary_lower or "vice president" in primary_lower:
            continue

        link = name_cell.select_one("a")
        if not link:
            continue
        href = link.get("href", "")
        if not href or "wiki" not in href:
            continue
        name = clean_text(link.get_text(" ", strip=True))
        if not name or len(name) < 4 or name[0].isdigit():
            continue
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        presidency_order += 1
        if "list of" in name.lower() or "election" in name.lower() or "vacant" in name.lower():
            continue

        office_range = clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else "Unknown"
        president_url = urljoin("https://en.wikipedia.org", href)
        profile = fetch_president_profile(president_url)
        if profile["name"] and not card_exists_by_name(profile["name"]):
            profile["years"] = office_range if office_range and office_range != "Unknown" else profile.get("years", "Unknown")
            collected.append(
                {
                    "name": profile["name"],
                    "image_url": profile["image_url"],
                    "category": "US Presidents",
                    "hint": profile["party"],
                    "answer": profile["name"],
                    "details": f"{profile['years']} | {profile['party']} | {profile['summary']}",
                    "source_url": profile["source_url"],
                    "highlights": json.dumps(profile["highlights"], ensure_ascii=False),
                    "presidency_order": presidency_order,
                }
            )

    if not collected:
        return []

    conn = get_connection()
    conn.execute("DELETE FROM Cards WHERE category = 'US Presidents'")
    conn.executemany(
        """
        INSERT INTO Cards (name, image_url, category, hint, answer, details, source_url, highlights, presidency_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item["name"],
                item["image_url"],
                item["category"],
                item["hint"],
                item["answer"],
                item["details"],
                item.get("source_url"),
                item.get("highlights"),
                item.get("presidency_order"),
            )
            for item in collected
        ],
    )
    conn.commit()
    conn.close()
    return collected


def get_all_cards(category: str = "US Presidents") -> List[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM Cards WHERE category = ? ORDER BY name ASC",
        (category,),
    ).fetchall()
    stale_rows = [row for row in rows if row["details"] and str(row["details"]).startswith("Unknown |") or row["presidency_order"] is None]
    if stale_rows:
        refresh_president_metadata_from_list()
        rows = conn.execute(
            "SELECT * FROM Cards WHERE category = ? ORDER BY name ASC",
            (category,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def register_user(first_name: str, last_name: str, username: str):
    if not first_name or not last_name or not username:
        return False, "Please complete all fields."

    username = username.strip()
    if not username:
        return False, "Username cannot be empty."

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO Users (first_name, last_name, username, date_created) VALUES (?, ?, ?, ?)",
            (first_name.strip(), last_name.strip(), username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        user_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO Profiles (user_id, profile_name, bio, date_created) VALUES (?, ?, ?, ?)",
            (user_id, f"{first_name.strip()} {last_name.strip()}", "Student profile", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return True, user_id
    except sqlite3.IntegrityError:
        return False, "This username is already in use."
    finally:
        conn.close()


def get_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM Users ORDER BY username ASC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def login_user(username: str):
    if not username:
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM Users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_quiz_result(user_id: int, score: int, attempted: int, correct: int):
    if user_id is None:
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO QuizResults (user_id, score, questions_attempted, correct_answers, date) VALUES (?, ?, ?, ?, ?)",
        (user_id, score, attempted, correct, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def save_competition_result(user_id: int, score: int, correct_answers: int, completion_time: float):
    if user_id is None:
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO CompetitionResults (user_id, score, correct_answers, completion_time, date) VALUES (?, ?, ?, ?, ?)",
        (user_id, score, correct_answers, completion_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def get_quiz_metrics():
    conn = get_connection()
    total_quiz_rows = conn.execute("SELECT COUNT(*) FROM QuizResults").fetchone()[0]
    avg_quiz_score = conn.execute("SELECT AVG(score) FROM QuizResults").fetchone()[0] or 0
    total_competition_rows = conn.execute("SELECT COUNT(*) FROM CompetitionResults").fetchone()[0]
    avg_competition_score = conn.execute("SELECT AVG(score) FROM CompetitionResults").fetchone()[0] or 0
    conn.close()
    return total_quiz_rows, avg_quiz_score, total_competition_rows, avg_competition_score


def get_db_stats():
    conn = get_connection()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0],
        "cards": conn.execute("SELECT COUNT(*) FROM Cards").fetchone()[0],
        "quiz_results": conn.execute("SELECT COUNT(*) FROM QuizResults").fetchone()[0],
        "competition_results": conn.execute("SELECT COUNT(*) FROM CompetitionResults").fetchone()[0],
    }
    conn.close()
    return stats


def get_leaderboard_df():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.first_name, u.last_name,
               cr.score, cr.date
        FROM CompetitionResults cr
        INNER JOIN Users u ON u.id = cr.user_id
        ORDER BY cr.score DESC, cr.date DESC
        LIMIT 20
        """
    ).fetchall()
    conn.close()

    records = []
    for idx, row in enumerate(rows, start=1):
        records.append(
            {
                "Rank": idx,
                "Student Name": f"{row['first_name']} {row['last_name']}",
                "Username": row["username"],
                "Score": row["score"],
                "Date": row["date"],
            }
        )
    return pd.DataFrame(records)


def ordinal_suffix(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def parse_years_from_details(details: str):
    if not details:
        return "Unknown"
    year_matches = re.findall(r"(\d{4})", details)
    if len(year_matches) >= 2:
        return f"{year_matches[0]} - {year_matches[-1]}"
    if year_matches:
        return year_matches[0]
    return "Unknown"


def extract_summary_from_details(details: str) -> str:
    if not details:
        return "No summary available."
    parts = [part.strip() for part in details.split("|") if part.strip()]
    if len(parts) >= 3:
        return parts[-1]
    return details


def build_president_timeline_df(cards):
    if not cards:
        return pd.DataFrame({"year": [], "presidency_number": [], "president": [], "served": [], "start_year": [], "end_year": []})

    timeline_rows = []
    for index, card in enumerate(cards, start=1):
        details = card.get("details", "")
        matches = re.findall(r"(\d{4})", details)
        if len(matches) < 2:
            continue
        start_year = int(matches[0])
        end_year = int(matches[-1])
        served = parse_years_from_details(details)
        timeline_rows.append(
            {
                "year": start_year,
                "presidency_number": index,
                "president": card.get("name", "Unknown"),
                "served": served,
                "start_year": start_year,
                "end_year": end_year,
            }
        )

    if not timeline_rows:
        return pd.DataFrame({"year": [], "presidency_number": [], "president": [], "served": [], "start_year": [], "end_year": []})

    df = pd.DataFrame(timeline_rows).sort_values("start_year").reset_index(drop=True)
    return df


def render_home():
    st.title("📚 WikiCard Learning System")
    st.markdown(
        """
        Build a smarter study routine with Wikipedia-powered flashcards, quizzes, and competition tracking.
        """
    )

    stats = get_db_stats()
    columns = st.columns(4)
    columns[0].metric("Users", stats["users"])
    columns[1].metric("Cards", stats["cards"])
    columns[2].metric("Quiz Attempts", stats["quiz_results"])
    columns[3].metric("Competition Entries", stats["competition_results"])

    st.subheader("Program Overview")
    st.write(
        "This app lets students register, study presidential cards, answer quiz questions, compete in timed rounds, and view analytics with a leader board."
    )

    if "current_user" in st.session_state and st.session_state["current_user"]:
        user = st.session_state["current_user"]
        st.success(f"Welcome back, {user['first_name']} {user['last_name']}!")


def render_register_login():
    st.title("👤 Register / Login")

    with st.form("register_form", clear_on_submit=True):
        st.subheader("Register a new account")
        first_name = st.text_input("First Name")
        last_name = st.text_input("Last Name")
        username = st.text_input("Username")
        submitted = st.form_submit_button("Create Account")

        if submitted:
            success, result = register_user(first_name, last_name, username)
            if success:
                st.session_state["current_user"] = login_user(username)
                st.success(f"Account created successfully for {first_name} {last_name}.")
            else:
                st.error(result)

    st.markdown("---")

    users = get_users()
    if not users:
        st.info("No users have registered yet. Create the first account above.")
        return

    with st.form("login_form"):
        st.subheader("Existing Account")
        usernames = [user["username"] for user in users]
        selected_user = st.selectbox("Select a username", usernames)
        login_button = st.form_submit_button("Log In")

        if login_button:
            user = login_user(selected_user)
            if user:
                st.session_state["current_user"] = user
                st.success(f"Logged in as {user['first_name']} {user['last_name']}.")
            else:
                st.error("Login failed. Please try again.")


def render_build_db():
    st.title("🛠️ Build Database")
    st.write("This page harvests educational content from Wikipedia for the selected category.")

    topics = ["US Presidents", "State Capitals", "Countries", "National Parks", "Programming Languages"]
    selected_topic = st.selectbox("Choose topic", topics)

    if selected_topic != "US Presidents":
        st.warning("Only US Presidents is currently implemented in this version of the app.")

    with st.container():
        st.subheader("Database Actions")
        col1, col2, col3 = st.columns(3)

        if col1.button("Refresh Wikipedia Data"):
            try:
                data = fetch_president_cards(force_refresh=True, topic=selected_topic)
                if data:
                    st.success(f"Refreshed database with {len(data)} {selected_topic.lower()} records.")
                else:
                    if selected_topic == "US Presidents":
                        st.info("Database already contains data or no new records were fetched.")
                    else:
                        st.warning("This topic is not yet implemented.")
            except Exception as exc:  # pragma: no cover
                st.error(f"Data refresh failed: {exc}")

        if col2.button("Delete All Cards"):
            conn = get_connection()
            conn.execute("DELETE FROM Cards")
            conn.commit()
            conn.close()
            st.success("All card records were deleted.")

        if col3.button("Rebuild Card Database"):
            try:
                data = fetch_president_cards(force_refresh=True, topic=selected_topic)
                if data:
                    st.success(f"Card database rebuilt with {len(data)} records.")
                else:
                    if selected_topic == "US Presidents":
                        st.info("No new president cards were available to import.")
                    else:
                        st.warning("This topic is not yet implemented.")
            except Exception as exc:  # pragma: no cover
                st.error(f"Rebuild failed: {exc}")

    st.markdown("---")
    stats = get_db_stats()
    st.subheader("Current Database Statistics")
    st.write({
        "Users": stats["users"],
        "Cards": stats["cards"],
        "Quiz Results": stats["quiz_results"],
        "Competition Results": stats["competition_results"],
    })

    if st.button("Load Initial President Data"):
        try:
            data = fetch_president_cards(force_refresh=False, topic=selected_topic)
            if data:
                st.success(f"Imported {len(data)} president cards.")
            else:
                if selected_topic == "US Presidents":
                    st.info("The database already contains presidential cards.")
                else:
                    st.warning("This topic is not yet implemented.")
        except Exception as exc:  # pragma: no cover
            st.error(f"Initial import failed: {exc}")


def render_study_mode():
    st.title("🧠 Study Mode")

    if "current_user" not in st.session_state or not st.session_state["current_user"]:
        st.warning("Please log in before using study mode.")
        return

    if get_db_stats()["cards"] == 0:
        ensure_initial_cards_loaded()

    cards = get_all_cards()
    if not cards:
        st.info("There are no cards to study yet. Please build the database first.")
        return

    if "study_index" not in st.session_state:
        st.session_state.study_index = 0
    if "study_view_count" not in st.session_state:
        st.session_state.study_view_count = 0

    card = cards[st.session_state.study_index]
    years = card["details"].split(" | ")[0] if " | " in card["details"] else "Unknown"
    source_url = card.get("source_url") or f"https://en.wikipedia.org/wiki/{card['answer'].replace(' ', '_')}"
    order_number = card.get("presidency_order")
    order_label = f"{ordinal_suffix(order_number)} President of the United States" if order_number else "President of the United States"

    try:
        highlights = json.loads(card.get("highlights") or "[]")
    except (TypeError, json.JSONDecodeError):
        highlights = []
    summary_text = extract_summary_from_details(card.get("details", ""))
    if len(summary_text) > 150:
        summary_text = summary_text[:147].rstrip() + "..."
    elif len(summary_text) < 80:
        summary_text = summary_text
    highlights = [str(item).strip() for item in highlights[:5] if str(item).strip()]

    reveal_answer = False
    if "show_answer_revealed" not in st.session_state:
        st.session_state.show_answer_revealed = {}
    current_card_key = card["name"]
    reveal_current = st.session_state.show_answer_revealed.get(current_card_key, False)

    left_col, right_col = st.columns([2, 1])

    with left_col:
        st.caption(f"Cards viewed: {st.session_state.study_view_count}")
        st.subheader(f"Card {st.session_state.study_index + 1} of {len(cards)}")
        st.image(card["image_url"], width=350)
        st.write(f"Number in order: {order_label}")
        st.write(f"Years in office: {years}")
        st.write(f"Political party: {card['hint']}")

        reveal_answer = st.button("Show Answer")
        if reveal_answer:
            st.session_state.show_answer_revealed[current_card_key] = True
        if reveal_current:
            st.success(f"President: {card['answer']}")
            st.write(card["details"])
            st.markdown(f"[Open {card['answer']} on Wikipedia]({source_url})")

        col1, col2, col3 = st.columns(3)
        if col1.button("Previous Card"):
            st.session_state.study_index = (st.session_state.study_index - 1) % len(cards)
            st.session_state.study_view_count += 1
            st.session_state.show_answer_revealed[current_card_key] = False
        if col2.button("Next Card"):
            st.session_state.study_index = (st.session_state.study_index + 1) % len(cards)
            st.session_state.study_view_count += 1
            st.session_state.show_answer_revealed[current_card_key] = False
        if col3.button("Random Card"):
            st.session_state.study_index = random.randrange(len(cards))
            st.session_state.study_view_count += 1
            st.session_state.show_answer_revealed[current_card_key] = False

    with right_col:
        if reveal_current:
            st.write(summary_text)
            st.markdown(f"[Open {card['answer']} on Wikipedia]({source_url})")

        st.markdown("---")


def render_quiz_mode():
    st.title("✅ Quiz Mode")

    if "current_user" not in st.session_state or not st.session_state["current_user"]:
        st.warning("Please log in before using the quiz.")
        return

    if get_db_stats()["cards"] == 0:
        ensure_initial_cards_loaded()

    cards = get_all_cards()
    if not cards:
        st.info("No cards are available. Please build the database first.")
        return

    if "quiz_cards" not in st.session_state or "quiz_index" not in st.session_state:
        st.session_state.quiz_cards = random.sample(cards, min(10, len(cards)))
        st.session_state.quiz_index = 0
        st.session_state.quiz_correct = 0
        st.session_state.quiz_attempted = 0
        st.session_state.quiz_incorrect = 0
        st.session_state.quiz_finished = False
        st.session_state.quiz_questions = []

    if st.session_state.quiz_finished:
        st.success("Quiz complete. Results saved.")
        if st.button("Start New Quiz"):
            st.session_state.quiz_cards = random.sample(cards, min(10, len(cards)))
            st.session_state.quiz_index = 0
            st.session_state.quiz_correct = 0
            st.session_state.quiz_attempted = 0
            st.session_state.quiz_incorrect = 0
            st.session_state.quiz_finished = False
            st.session_state.quiz_questions = []
        return

    if not st.session_state.quiz_cards:
        st.session_state.quiz_cards = random.sample(cards, min(10, len(cards)))

    current = st.session_state.quiz_cards[st.session_state.quiz_index]
    st.subheader(f"Question {st.session_state.quiz_index + 1} of {len(st.session_state.quiz_cards)}")
    st.image(current["image_url"], width=350)
    st.write(f"Hint: {current['hint']}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Questions Attempted", st.session_state.quiz_attempted)
    c2.metric("Correct Answers", st.session_state.quiz_correct)
    c3.metric("Incorrect Answers", st.session_state.quiz_incorrect)

    user_answer = st.text_input("Who is this president?", key="quiz_answer")
    if st.button("Check Answer"):
        attempt = user_answer.strip()
        if not attempt:
            st.warning("Type an answer before checking.")
        else:
            st.session_state.quiz_attempted += 1
            is_correct = attempt.lower() == current["answer"].lower()
            if is_correct:
                st.session_state.quiz_correct += 1
                st.success("Correct!")
            else:
                st.session_state.quiz_incorrect += 1
                st.error(f"Incorrect. The correct answer is: {current['answer']}")
            st.write(f"Questions attempted: {st.session_state.quiz_attempted}")
            st.write(f"Correct answers: {st.session_state.quiz_correct}")
            st.write(f"Incorrect answers: {st.session_state.quiz_incorrect}")

            if st.session_state.quiz_index + 1 >= len(st.session_state.quiz_cards):
                score = int((st.session_state.quiz_correct / max(st.session_state.quiz_attempted, 1)) * 100)
                save_quiz_result(
                    st.session_state["current_user"]["id"],
                    score,
                    st.session_state.quiz_attempted,
                    st.session_state.quiz_correct,
                )
                st.session_state.quiz_finished = True
                st.balloons()
            else:
                st.session_state.quiz_index += 1
                st.session_state.quiz_questions.append(current["answer"])
                st.rerun()

    if st.button("Reset Quiz"):
        st.session_state.quiz_cards = random.sample(cards, min(10, len(cards)))
        st.session_state.quiz_index = 0
        st.session_state.quiz_correct = 0
        st.session_state.quiz_attempted = 0
        st.session_state.quiz_incorrect = 0
        st.session_state.quiz_finished = False
        st.session_state.quiz_questions = []


def render_competition_mode():
    st.title("🏁 Competition Mode")

    if "current_user" not in st.session_state or not st.session_state["current_user"]:
        st.warning("Please log in before entering competition mode.")
        return

    if get_db_stats()["cards"] == 0:
        ensure_initial_cards_loaded()

    cards = get_all_cards()
    if not cards:
        st.info("No cards are available. Please build the database first.")
        return

    if "competition_cards" not in st.session_state:
        st.session_state.competition_cards = random.sample(cards, min(10, len(cards)))
        st.session_state.competition_index = 0
        st.session_state.competition_score = 0
        st.session_state.competition_correct = 0
        st.session_state.competition_started = False
        st.session_state.competition_elapsed = 0.0
        st.session_state.competition_total_time = 0.0
        st.session_state.competition_questions = []

    if not st.session_state.competition_started:
        if st.button("Start 10-Question Competition"):
            st.session_state.competition_cards = random.sample(cards, min(10, len(cards)))
            st.session_state.competition_index = 0
            st.session_state.competition_score = 0
            st.session_state.competition_correct = 0
            st.session_state.competition_started = True
            st.session_state.competition_elapsed = 0.0
            st.session_state.competition_total_time = 0.0
            st.session_state.competition_questions = []
            st.session_state.competition_start_time = time.monotonic()
            st.rerun()
        return

    if st.session_state.competition_index >= len(st.session_state.competition_cards):
        completion_seconds = round(st.session_state.competition_total_time, 2)
        save_competition_result(
            st.session_state["current_user"]["id"],
            st.session_state.competition_score,
            st.session_state.competition_correct,
            completion_seconds,
        )
        st.success(f"Competition complete! Final score: {st.session_state.competition_score}")
        st.write(f"Correct answers: {st.session_state.competition_correct} / {len(st.session_state.competition_cards)}")
        st.write(f"Completion time: {completion_seconds} seconds")

        if st.button("Play Again"):
            st.session_state.competition_cards = random.sample(cards, min(10, len(cards)))
            st.session_state.competition_index = 0
            st.session_state.competition_score = 0
            st.session_state.competition_correct = 0
            st.session_state.competition_started = True
            st.session_state.competition_elapsed = 0.0
            st.session_state.competition_total_time = 0.0
            st.session_state.competition_questions = []
            st.session_state.competition_start_time = time.monotonic()
            st.rerun()
        return

    current = st.session_state.competition_cards[st.session_state.competition_index]
    if "competition_start_time" not in st.session_state:
        st.session_state.competition_start_time = time.monotonic()

    elapsed = time.monotonic() - st.session_state.competition_start_time
    remaining = max(30 - elapsed, 0)
    st.caption(f"Question {st.session_state.competition_index + 1} of {len(st.session_state.competition_cards)}")
    st.write(f"Time left: {remaining:.1f}s")
    st.image(current["image_url"], width=340)
    st.write(f"Hint: {current['hint']}")

    other_names = [card["answer"] for card in cards if card["answer"] != current["answer"]]
    answer_choices = [current["answer"]] + random.sample(other_names, 4)
    random.shuffle(answer_choices)

    selected_answer = st.radio(
        "Who is this president?",
        options=answer_choices,
        index=None,
        horizontal=False,
    )

    if st.button("Submit Answer"):
        if not selected_answer:
            st.warning("Select one answer before submitting.")
        else:
            elapsed_seconds = time.monotonic() - st.session_state.competition_start_time
            st.session_state.competition_total_time += elapsed_seconds
            is_correct = selected_answer.lower() == current["answer"].lower()
            if is_correct:
                bonus = max(0, int(50 * (1 - min(elapsed_seconds, 30) / 30)))
                question_score = 100 + bonus
                st.session_state.competition_correct += 1
                st.success(f"Correct! +{question_score} points")
            else:
                question_score = 0
                st.error(f"Incorrect. The answer is {current['answer']}.")

            st.session_state.competition_score += question_score
            st.session_state.competition_index += 1
            st.session_state.competition_start_time = time.monotonic()
            st.session_state.competition_elapsed = 0.0
            st.rerun()

    if remaining <= 0:
        st.session_state.competition_total_time += 30
        st.warning(f"Time expired. Correct answer: {current['answer']}")
        st.session_state.competition_index += 1
        st.session_state.competition_start_time = time.monotonic()
        st.rerun()


def render_leaderboard():
    st.title("🏆 Leaderboard")

    leaderboard = get_leaderboard_df()
    if leaderboard.empty:
        st.info("No competition results have been submitted yet.")
        return

    top_score = leaderboard["Score"].max()
    average_score = leaderboard["Score"].mean()
    total_players = leaderboard["Student Name"].nunique()

    c1, c2, c3 = st.columns(3)
    c1.metric("Highest Score Ever", int(top_score))
    c2.metric("Average Score", round(float(average_score), 2))
    c3.metric("Total Players", int(total_players))

    st.subheader("Top 20 Competition Scores")
    st.dataframe(leaderboard[["Rank", "Student Name", "Score", "Date"]], use_container_width=True, hide_index=True)


def render_analytics():
    st.title("📊 Analytics Dashboard")

    conn = get_connection()
    users = conn.execute("SELECT COUNT(*) FROM Users").fetchone()[0]
    cards = conn.execute("SELECT COUNT(*) FROM Cards").fetchone()[0]
    quiz_total = conn.execute("SELECT COUNT(*) FROM QuizResults").fetchone()[0]
    avg_quiz = conn.execute("SELECT AVG(score) FROM QuizResults").fetchone()[0] or 0
    avg_competition = conn.execute("SELECT AVG(score) FROM CompetitionResults").fetchone()[0] or 0
    conn.close()

    metrics = st.columns(5)
    metrics[0].metric("Total Users", users)
    metrics[1].metric("Total Cards", cards)
    metrics[2].metric("Total Quizzes Taken", quiz_total)
    metrics[3].metric("Average Quiz Score", round(float(avg_quiz), 2))
    metrics[4].metric("Average Competition Score", round(float(avg_competition), 2))

    conn = get_connection()
    quiz_df = pd.DataFrame(
        conn.execute(
            "SELECT date, score FROM QuizResults ORDER BY date ASC"
        ).fetchall(),
        columns=["date", "score"],
    )
    if not quiz_df.empty:
        quiz_df["date"] = pd.to_datetime(quiz_df["date"])
        quiz_df = quiz_df.groupby("date", as_index=False)["score"].mean()
    else:
        quiz_df = pd.DataFrame({"date": [], "score": []})

    competition_df = pd.DataFrame(
        conn.execute(
            "SELECT score FROM CompetitionResults"
        ).fetchall(),
        columns=["score"],
    )

    student_df = pd.DataFrame(
        conn.execute(
            "SELECT u.first_name, u.last_name, MAX(cr.score) AS best_score FROM CompetitionResults cr JOIN Users u ON u.id = cr.user_id GROUP BY u.id ORDER BY best_score DESC LIMIT 10"
        ).fetchall(),
        columns=["first_name", "last_name", "best_score"],
    )
    conn.close()

    st.subheader("Quiz Scores Over Time")
    if not quiz_df.empty:
        fig1 = px.line(quiz_df, x="date", y="score", title="Average Quiz Score by Date")
        st.plotly_chart(fig1, use_container_width=True)
    else:
        st.info("No quiz data available yet.")

    st.subheader("Competition Scores Distribution")
    if not competition_df.empty:
        fig2 = px.histogram(competition_df, x="score", nbins=10, title="Competition Score Distribution")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No competition data available yet.")

    st.subheader("Top Performing Students")
    if not student_df.empty:
        student_df["student_name"] = student_df["first_name"] + " " + student_df["last_name"]
        fig3 = px.bar(student_df, x="student_name", y="best_score", title="Top Students by Best Competition Score")
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("No student performance data available yet.")


def app_navigation():
    pages = [
        "Home",
        "Register/Login",
        "Build Database",
        "Study Mode",
        "Quiz Mode",
        "Competition Mode",
        "Leaderboard",
        "Analytics",
    ]
    choice = st.sidebar.radio("Navigation", pages)
    return choice


def main():
    st.set_page_config(page_title="WikiCard Learning System", page_icon="📚", layout="wide")

    if get_db_stats()["cards"] == 0:
        ensure_initial_cards_loaded()

    st.sidebar.image(
        "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?auto=format&fit=crop&w=200&q=80",
        width=240,
    )
    st.sidebar.title("WikiCard")
    st.sidebar.caption("Educational study platform")

    if "current_user" not in st.session_state:
        st.session_state["current_user"] = None

    page = app_navigation()

    if page == "Home":
        render_home()
    elif page == "Register/Login":
        render_register_login()
    elif page == "Build Database":
        render_build_db()
    elif page == "Study Mode":
        render_study_mode()
    elif page == "Quiz Mode":
        render_quiz_mode()
    elif page == "Competition Mode":
        render_competition_mode()
    elif page == "Leaderboard":
        render_leaderboard()
    elif page == "Analytics":
        render_analytics()


if __name__ == "__main__":
    main()
