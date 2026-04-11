import os

API_URL = os.getenv("AI_API_URL", "").strip()
API_KEY = os.getenv("AI_API_KEY", "").strip()


def rewrite_text(text, tone):
    """
    Midlertidig placeholder.
    Når API_URL og API_KEY bliver sat senere,
    kan funktionen udvides til at kalde en rigtig model.
    """

    if not API_URL:
        return f"[Prototype - {tone}] {text}"

    # Her kommer rigtig API-kald senere
    return f"[API klar, men ikke implementeret endnu - {tone}] {text}"