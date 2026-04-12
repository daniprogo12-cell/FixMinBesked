import os
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI
from utils.prompts import get_prompt

API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

if not API_KEY:
    raise ValueError("Missing OPENAI_API_KEY in environment variables.")

client = OpenAI(api_key=API_KEY)


def rewrite_text(text: str, tone: str) -> str:
    prompt = get_prompt(tone, text)

    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Du er et dansk værktøj til omskrivning af tekst. "
                    "Du må kun omskrive teksten og skal skrive naturligt, flydende og korrekt dansk."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()