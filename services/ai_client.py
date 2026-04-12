import os
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI

API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

if not API_KEY:
    raise ValueError("Missing OPENAI_API_KEY in environment variables.")

client = OpenAI(api_key=API_KEY)


def rewrite_text(text: str, tone: str, system_prompt: str) -> str:
    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": text,
            },
        ],
        temperature=0.2,
        timeout=15,
        max_tokens=800,
    )

    return response.choices[0].message.content.strip()