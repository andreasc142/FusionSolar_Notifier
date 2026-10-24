"""
Simple email ping test - confirms GitHub Actions secrets + Gmail sending
work correctly, before wiring up the actual FusionSolar scraping logic.
"""

# Activity commit

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime

EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.send_message(msg)


if __name__ == "__main__":
    now = datetime.now()
    send_email(
        subject="[FusionSolar Notifier] Test ping",
        body=(
            f"This is a test email sent from GitHub Actions.\n"
            f"Sent at {now:%Y-%m-%d %H:%M:%S}.\n\n"
            f"If you got this, the pipeline (secrets + email) is wired up correctly."
        ),
    )
    print("Test email sent successfully.")
