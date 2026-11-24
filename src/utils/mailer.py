"""
Envio de correos con la API de Gmail (OAuth2 + refresh token).

Variables (.env):
  GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET   cliente OAuth con el que se genero el refresh token
                                          (si faltan, se usan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)
  GOOLE_REFRESH_TOKEN                     tambien acepta GMAIL_REFRESH_TOKEN / GOOGLE_REFRESH_TOKEN
  GMAIL_SENDER        (opcional) correo remitente, para mostrar "Nombre <correo>"
  GMAIL_SENDER_NAME   (opcional) por defecto "Control de Proyectos FaCyT"
  APP_URL             (opcional) por defecto http://localhost:3000

Prueba rapida:  python -m src.utils.mailer tu-correo@ejemplo.com
"""
import os
import sys
import base64
import asyncio
import logging
from email.message import EmailMessage
from email.utils import formataddr

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


CLIENT_ID = _env("GMAIL_CLIENT_ID", "GOOGLE_CLIENT_ID")
CLIENT_SECRET = _env("GMAIL_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET")
REFRESH_TOKEN = _env("GMAIL_REFRESH_TOKEN", "GOOGLE_REFRESH_TOKEN", "GOOLE_REFRESH_TOKEN")
SENDER = _env("GMAIL_SENDER")
SENDER_NAME = _env("GMAIL_SENDER_NAME") or "Control de Proyectos FaCyT"
APP_URL = (_env("APP_URL") or "http://localhost:3000").rstrip("/")


def mail_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)


def _send_sync(to: str, subject: str, text: str, html: str) -> dict:
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    # Un cliente por envio: httplib2 no es thread-safe.
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if SENDER:
        msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


async def send_email(to: str, subject: str, text: str, html: str) -> bool:
    """Envia un correo. Nunca lanza: devuelve False y lo deja en el log si falla."""
    if not mail_configured():
        logger.warning("Gmail no configurado: no se envio el correo a %s", to)
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, text, html)
        logger.info("Correo enviado a %s", to)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo enviar el correo a %s: %s", to, e)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Uso: python -m src.utils.mailer destinatario@ejemplo.com")
        sys.exit(1)
    if not mail_configured():
        print("Faltan variables: client id, client secret o refresh token.")
        sys.exit(1)
    _send_sync(sys.argv[1], "Prueba de correo - Control de Proyectos FaCyT",
               "Si lees esto, el envio con Gmail funciona.",
               "<p>Si lees esto, el envío con Gmail <b>funciona</b>.</p>")
    print("Correo de prueba enviado a", sys.argv[1])
