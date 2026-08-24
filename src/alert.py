"""
Email alerts for failed or degraded daily runs.

The tracker ran broken for 18 days without anyone noticing, because a failure
only ever showed up as a non-zero exit into a log file. This sends it somewhere
you'll actually see.

Configure in .env. If any of these are missing, alerting quietly no-ops — a
missing alert config must never be the thing that breaks a run:

  ALERT_SMTP_HOST=smtp.gmail.com
  ALERT_SMTP_PORT=587
  ALERT_SMTP_USER=you@yourdomain.com
  ALERT_SMTP_PASSWORD=<app password, NOT your account password>
  ALERT_EMAIL_TO=you@yourdomain.com

Gmail/Workspace needs an app password (Google Account > Security > App
passwords) because 2FA blocks plain password auth. Port 587 uses STARTTLS,
465 uses implicit TLS; both are handled.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

REQUIRED = (
    "ALERT_SMTP_HOST",
    "ALERT_SMTP_PORT",
    "ALERT_SMTP_USER",
    "ALERT_SMTP_PASSWORD",
    "ALERT_EMAIL_TO",
)


def missing_config():
    return [k for k in REQUIRED if not os.environ.get(k)]


def send(subject, body):
    """Send an alert. Returns True if it went out, False otherwise."""
    missing = missing_config()
    if missing:
        print(f"alert: not configured, skipping (missing {', '.join(missing)})")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["ALERT_SMTP_USER"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(body)

    host = os.environ["ALERT_SMTP_HOST"]
    user = os.environ["ALERT_SMTP_USER"]
    password = os.environ["ALERT_SMTP_PASSWORD"]
    try:
        port = int(os.environ["ALERT_SMTP_PORT"])
    except ValueError:
        print("alert: ALERT_SMTP_PORT is not a number, skipping")
        return False

    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=context)
                s.login(user, password)
                s.send_message(msg)
    except Exception as e:
        # Never raise: a broken mail server shouldn't fail the run on top of
        # whatever we were trying to report.
        print(f"alert: send failed: {type(e).__name__}: {e}")
        return False

    print(f"alert: emailed {os.environ['ALERT_EMAIL_TO']}")
    return True
