import json
import os
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

CATEGORIES = ["Ворк", "Реактор"]

SYSTEM_PROMPT = f"""Ти асистент для обліку витрат. Твоя задача — розпарсити текст і повернути JSON з такими полями:
- amount: число (float), сума витрат
- currency: рядок, валюта (за замовчуванням "UAH")
- description: рядок, коротка суть витрати (до 60 символів)
- category: одне з двох значень: {json.dumps(CATEGORIES, ensure_ascii=False)}

Правила вибору категорії:
- "Ворк" — все що стосується роботи, послуг, документів, зарплат, сервісів, логістики, допоміжних витрат, або коли явно вказано "ворк", "work", "робота".
- "Реактор" — все що стосується проектів чи об'єктів "Реактор", поповнення каси реактора, оренди реактора, або коли явно вказано "реактор", "reactor".

Якщо з тексту складно визначити категорію — обери найбільш відповідне із двох ("Ворк" або "Реактор").

Якщо не можеш визначити суму — поверни null для amount.

Поверни ТІЛЬКИ валідний JSON без додаткового тексту. Приклад:
{{"amount": 250.0, "currency": "UAH", "description": "Кава з клієнтом", "category": "Ворк"}}
"""


def parse_expense(text: str) -> dict:
    """Parses expense text into structured fields using Groq LLM."""
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)

    # Add current datetime
    data["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Ensure currency default
    if not data.get("currency"):
        data["currency"] = "UAH"

    # Validate category
    if data.get("category") not in CATEGORIES:
        data["category"] = "Ворк"

    return data
