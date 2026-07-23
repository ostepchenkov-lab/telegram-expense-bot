import json
import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"


def _get_client() -> gspread.Client:
    """Returns an authenticated gspread client from file or env string."""
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json_str:
        info = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "groshi-test-503314-4826b51946cd.json")
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(creds)


def append_expense(expense: dict) -> str:
    """Appends an expense row to the spreadsheet. Returns the spreadsheet URL."""
    gc = _get_client()
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    sheet = spreadsheet.sheet1

    row = [
        expense.get("date", ""),
        expense.get("amount", ""),
        expense.get("currency", "UAH"),
        expense.get("description", ""),
        expense.get("category", "Ворк"),
    ]
    sheet.append_row(row, value_input_option="USER_ENTERED")

    return SPREADSHEET_URL
