"""
Alerts for failed or degraded daily runs.

The tracker ran broken for 18 days without anyone noticing, because a failure
only ever showed up as a non-zero exit into a log file. This sends it somewhere
you'll actually see.

Two transports, tried in order:

1. Email over SMTP, if all five ALERT_* vars below are set in .env. Gmail and
   Workspace need an app password (Google Account > Security > App passwords),
   which is only offered once 2-Step Verification is on, and which an admin can
   disable org-wide.

     ALERT_SMTP_HOST=smtp.gmail.com
     ALERT_SMTP_PORT=587
     ALERT_SMTP_USER=you@yourdomain.com
     ALERT_SMTP_PASSWORD=<app password, NOT your account password>
     ALERT_EMAIL_TO=you@yourdomain.com

2. A GitHub issue in the private data repo, which needs no new credentials —
   it reuses the `gh` login the daily push already relies on, and GitHub emails
   you about activity in your own repos. Override the target with
   ALERT_GH_REPO. To keep a multi-day outage from opening one issue per day,
   this comments on the existing open thread if there is one.

Both transports swallow their own errors: alerting must never be the thing
that breaks a run.
"""

import json
import os
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

EMAIL_VARS = (
    "ALERT_SMTP_HOST",
    "ALERT_SMTP_PORT",
    "ALERT_SMTP_USER",
    "ALERT_SMTP_PASSWORD",
    "ALERT_EMAIL_TO",
)

DEFAULT_GH_REPO = "paulacavero/aeo-data"

# Fixed title so repeated alerts land on one thread instead of spawning an
# issue a day. Close it when the run is healthy again.
GH_TITLE = "AEO tracker: run needs attention"


def missing_email_config():
    return [k for k in EMAIL_VARS if not os.environ.get(k)]


def _send_email(subject, body):
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
        print("alert: ALERT_SMTP_PORT is not a number")
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
        print(f"alert: email failed: {type(e).__name__}: {e}")
        return False

    print(f"alert: emailed {os.environ['ALERT_EMAIL_TO']}")
    return True


def _find_gh():
    """launchd jobs don't inherit Homebrew's PATH, so check known spots first."""
    for candidate in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
        if Path(candidate).exists():
            return candidate
    return shutil.which("gh")


def _gh(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _open_thread(gh, repo):
    """Number of the existing open alert issue, or None."""
    try:
        out = _gh([gh, "issue", "list", "--repo", repo, "--state", "open",
                   "--search", f'"{GH_TITLE}" in:title',
                   "--json", "number,title", "--limit", "5"])
        if out.returncode != 0 or not out.stdout.strip():
            return None
        for item in json.loads(out.stdout):
            if item.get("title") == GH_TITLE:
                return item["number"]
    except Exception:
        pass
    return None


def _send_github(subject, body):
    gh = _find_gh()
    if not gh:
        print("alert: gh not found and email not configured — no alert sent")
        return False

    repo = os.environ.get("ALERT_GH_REPO", DEFAULT_GH_REPO)
    text = f"{subject}\n\n```\n{body}\n```"

    try:
        number = _open_thread(gh, repo)
        if number:
            result = _gh([gh, "issue", "comment", str(number),
                          "--repo", repo, "--body", text])
            action = f"commented on issue #{number}"
        else:
            result = _gh([gh, "issue", "create", "--repo", repo,
                          "--title", GH_TITLE, "--body", text])
            action = "opened a new issue"
        if result.returncode != 0:
            print(f"alert: gh failed: {result.stderr.strip()[:300]}")
            return False
    except Exception as e:
        print(f"alert: gh error: {type(e).__name__}: {e}")
        return False

    print(f"alert: {action} in {repo}")
    return True


def send(subject, body):
    """
    Deliver an alert. Prefers email when configured, otherwise (or if the mail
    server errors) falls back to a GitHub issue. Returns True if anything got
    through.
    """
    if not missing_email_config():
        if _send_email(subject, body):
            return True
        print("alert: falling back to GitHub issue")
    return _send_github(subject, body)
