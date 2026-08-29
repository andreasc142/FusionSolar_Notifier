"""
FusionSolar Monitor - Barebones Alert Script
---------------------------------------------
Checks current solar production. Sends an email ONLY if something
looks wrong (scraper fails, or production is near-zero during daylight).
Silent otherwise - no spam on every run.
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from datetime import datetime
from fusion_solar_py.client import FusionSolarClient

FUSION_USERNAME = os.environ["FUSION_USERNAME"]
FUSION_PASSWORD = os.environ["FUSION_PASSWORD"]
FUSION_SUBDOMAIN = os.environ.get("FUSION_SUBDOMAIN", "region01eu5")

EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]

# Alert thresholds
DAYLIGHT_START_HOUR = 8
DAYLIGHT_END_HOUR = 18
MIN_EXPECTED_KW = 0.1


def send_alert_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.send_message(msg)


def check_solar_status() -> None:
    now = datetime.now()

    try:
        client = FusionSolarClient(FUSION_USERNAME, FUSION_PASSWORD, huawei_subdomain=FUSION_SUBDOMAIN)
        status = client.get_power_status()
        client.log_out()
    except Exception as e:
        send_alert_email(
            subject="[Solar Monitor] Scraper failed",
            body=f"Couldn't fetch FusionSolar data at {now:%Y-%m-%d %H:%M}.\n\nError: {e}",
        )
        return

    current_kw = status.current_power_kw
    is_daylight = DAYLIGHT_START_HOUR <= now.hour < DAYLIGHT_END_HOUR

    if is_daylight and current_kw < MIN_EXPECTED_KW:
        send_alert_email(
            subject="[Solar Monitor] Production looks off",
            body=(
                f"Current production is only {current_kw:.2f} kW at {now:%H:%M}, "
                f"during expected daylight hours.\n\n"
                f"Today's energy so far: {status.energy_today_kwh:.2f} kWh\n"
                f"Lifetime total: {status.energy_kwh:.1f} kWh\n\n"
                f"Worth checking the panels/inverter."
            ),
        )


if __name__ == "__main__":
    check_solar_status()
