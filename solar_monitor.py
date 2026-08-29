"""
FusionSolar Monitor

For each of the account's plants, checks current power output. If any plant
is producing under threshold during the daily monitoring window (dynamically
calculated as sunrise+1.5h to sunset-1.5h for Nicosia, Cyprus), sends an
alert email - unless one was already sent for that specific plant in the
last 3 hours (checked via searching the sender's own Gmail Sent folder,
so no separate state file/database is needed). Login failures are capped
at one alert per day, using the same Sent-folder check.
"""

import os
import imaplib
import smtplib
import ssl
from email import message_from_bytes
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from fusion_solar_py.client import FusionSolarClient

FUSION_USERNAME = os.environ["FUSION_USERNAME"]
FUSION_PASSWORD = os.environ["FUSION_PASSWORD"]
FUSION_SUBDOMAIN = os.environ["FUSION_SUBDOMAIN"]

EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]

CYPRUS_TZ = ZoneInfo("Asia/Nicosia")  # IANA's identifier for Cyprus - handles EET/EEST DST automatically
NICOSIA = LocationInfo("Nicosia", "Cyprus", "Asia/Nicosia", 35.1856, 33.3823)

START_TIME_BUFFER = timedelta(hours=1, minutes=30)  # start monitoring this long after today's actual sunrise
SUNSET_BUFFER = timedelta(hours=1, minutes=30)  # stop monitoring this long before today's actual sunset
MIN_EXPECTED_KW = 0.1
COOLDOWN_MINUTES = 3 * 60

GMAIL_SENT_FOLDER = '"[Gmail]/Sent Mail"'


def now_cyprus() -> datetime:
    return datetime.now(CYPRUS_TZ)


def get_start_time_today() -> dtime:
    """Computes today's start = sunrise + START_TIME_BUFFER."""
    s = sun(NICOSIA.observer, date=now_cyprus().date(), tzinfo=CYPRUS_TZ)
    cutoff = s["sunrise"] + START_TIME_BUFFER
    return cutoff.time()


def get_end_time_today() -> dtime:
    """Computes today's cutoff = sunset - SUNSET_BUFFER, recalculated daily
    so it self-adjusts across seasons instead of using one fixed hour."""
    s = sun(NICOSIA.observer, date=now_cyprus().date(), tzinfo=CYPRUS_TZ)
    cutoff = s["sunset"] - SUNSET_BUFFER
    return cutoff.time()


def get_email_recipients() -> list[str]:
    """Builds the recipient list from EMAIL_TO (required base recipient),
    then EMAIL_TO1, EMAIL_TO2, ... until a missing one is hit."""
    recipients = [EMAIL_TO]
    i = 1
    while True:
        extra = os.environ.get(f"EMAIL_TO{i}")
        if not extra:
            break
        recipients.append(extra)
        i += 1
    return recipients


def send_email(subject: str, body: str) -> None:
    recipients = get_email_recipients()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.send_message(msg)


def minutes_since_last_alert(plant_id: str) -> float | None:
    """Returns minutes since the most recent sent email containing this
    specific plant_id in the subject, sent today (Cyprus date). None if none found."""
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
    imap.select(GMAIL_SENT_FOLDER)

    today_str = now_cyprus().strftime("%d-%b-%Y")
    status, data = imap.search(None, f'(SUBJECT "{plant_id}" SINCE "{today_str}")')

    latest_time = None
    if status == "OK" and data[0]:
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(RFC822.HEADER)")
            msg = message_from_bytes(msg_data[0][1])
            sent_time = parsedate_to_datetime(msg["Date"])
            sent_time_cyprus = sent_time.astimezone(CYPRUS_TZ)  # normalize whatever offset Gmail stamped
            if latest_time is None or sent_time_cyprus > latest_time:
                latest_time = sent_time_cyprus

    imap.logout()

    if latest_time is None:
        return None
    return (now_cyprus() - latest_time).total_seconds() / 60


def already_sent_today(subject_substring: str) -> bool:
    """Checks whether an email with this subject substring was already
    sent today (Cyprus date), regardless of exact time. Used for the
    once-a-day scraper-failure alert."""
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
    imap.select(GMAIL_SENT_FOLDER)

    today_str = now_cyprus().strftime("%d-%b-%Y")
    status, data = imap.search(None, f'(SUBJECT "{subject_substring}" SINCE "{today_str}")')
    imap.logout()

    return status == "OK" and bool(data[0])


def check_solar_status() -> None:
    now = now_cyprus()
    start_time_today = get_start_time_today()
    end_time_today = get_end_time_today()
    print(f"Ran at {now:%Y-%m-%d %H:%M:%S %Z} | today's window: {start_time_today}-{end_time_today} (1h after sunrise / 1h before sunset)")

    # Filter 1: time window
    if not (start_time_today <= now.time() <= end_time_today):
        print(f"Outside monitoring window. Stopping.")
        return

    # Filter 2 (per-plant cooldown) happens below, inside the loop,
    # since we need to know which plants are down before checking their individual cooldowns.

    try:
        client = FusionSolarClient(FUSION_USERNAME, FUSION_PASSWORD, huawei_subdomain=FUSION_SUBDOMAIN)
        plant_ids = client.get_plant_ids()
    except Exception as e:
        if already_sent_today("FusionSolar scraper failed"):
            print("Scraper login failed, but already alerted today. Staying quiet.")
            return
        send_email(
            subject="FusionSolar scraper failed",
            body=f"Couldn't log in / fetch plant list at {now:%Y-%m-%d %H:%M %Z}.\n\nError: {e}",
        )
        print("Scraper login failed. Alert sent (first time today).")
        return

    for pid in plant_ids:
        try:
            data = client.get_current_plant_data(pid)
            current_kw = data.get("currentPower", 0.0)
        except Exception:
            print("  One plant failed to fetch data.")
            continue

        if current_kw >= MIN_EXPECTED_KW:
            continue  # this plant is fine

        mins_since = minutes_since_last_alert(pid)
        if mins_since is not None and mins_since < COOLDOWN_MINUTES:
            print("  A plant is down but still within its cooldown. Staying quiet.")
            continue

        send_email(
            subject=f"FusionSolar down, plant: {pid}",
            body=(
                f"Checked at {now:%Y-%m-%d %H:%M:%S %Z}\n\n"
                f"Plant {pid} is producing {current_kw:.2f} kW, "
                f"under the {MIN_EXPECTED_KW} kW threshold."
            ),
        )
        print("  Alert sent for one plant.")

    client.log_out()


if __name__ == "__main__":
    check_solar_status()
