import os
import requests
from utils.prompts import get_prompt

API_URL = os.getenv("AI_API_URL", "").strip()
API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()

def rewrite_text(text: str, tone: str) -> str:
    print("API_URL =", repr(API_URL))
    print("AI_MODEL =", repr(AI_MODEL))
    print("API_KEY exists =", bool(API_KEY))

    if not API_URL or not API_KEY or not AI_MODEL:
        return f"[Prototype - {tone}] {text}"

    prompt = get_prompt(tone, text)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
        "X-OpenRouter-Title": "FixMinBesked",
    }

    payload = {
        "model": AI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Du er et dansk værktøj til omskrivning af tekst. Du må kun omskrive teksten."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.4
    }

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        print("STATUS =", response.status_code)
        print("BODY =", response.text)
        response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    except requests.RequestException as e:
        return f"Fejl ved API-kald: {str(e)}"
    except (KeyError, IndexError, TypeError, ValueError):
        return "Ugyldigt svar fra AI-servicen."