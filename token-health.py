#!/usr/bin/env python3

"""
Google Calendar Token Health Monitor

Checks whether the shared `token.json` OAuth credential can still be
refreshed. Intended to be run daily via cron, ahead of the other calendar
scripts, so a dead or dying token is caught and emailed as an alert
instead of being discovered when print.py/events.py/week-ahead.py fail.

By default this script is silent on success and only sends an email
when the token is missing, invalid, or fails to refresh.

Requirements:
- Python 3.x
- Google Calendar API credentials (credentials.json, token.json)
- SMTP configuration in config.ini

License:
    GNU General Public License v3.0
"""

import os
import smtplib
import configparser
import logging
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logging.basicConfig(level=logging.INFO)

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
TOKEN_FILE = 'token.json'


def load_config(config_file: str = "config.ini") -> configparser.ConfigParser:
    """Loads SMTP and recipient configuration from config.ini."""
    config = configparser.ConfigParser()
    config.read(config_file)
    return config


def check_token_health() -> tuple[bool, str]:
    """
    Attempts to load and refresh token.json.

    Returns:
        tuple[bool, str]: (True, "") if the token is healthy and refreshable.
        (False, reason) if the token is missing, invalid, or fails to refresh.
    """
    if not os.path.exists(TOKEN_FILE):
        return False, f"{TOKEN_FILE} does not exist. Re-authentication is required."

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except Exception as e:
        return False, f"{TOKEN_FILE} could not be loaded (corrupt or invalid format): {e}"

    if not creds.refresh_token:
        return False, f"{TOKEN_FILE} has no refresh_token. Re-authentication is required."

    try:
        creds.refresh(Request())
    except Exception as e:
        return False, f"Token refresh failed: {e}. Re-authentication is required."

    # Persist the refreshed token so this check also keeps it current
    with open(TOKEN_FILE, 'w') as token:
        token.write(creds.to_json())

    return True, ""


def send_alert_email(reason: str, smtp_details: dict, from_email: str, recipients: list[str]) -> None:
    """Sends an alert email reporting a token health failure."""
    subject = "ALERT: Google Calendar token needs re-authentication"
    body = (
        "The Google Calendar token health check failed.\n\n"
        f"Reason: {reason}\n\n"
        "Please re-run the OAuth flow (e.g. by running one of the calendar "
        "scripts locally) to generate a new token.json before scheduled "
        "calendar scripts start failing."
    )

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = ', '.join(recipients)

    try:
        if smtp_details['SSL']:
            with smtplib.SMTP_SSL(smtp_details['server'], smtp_details['port']) as server:
                server.login(smtp_details['username'], smtp_details['password'])
                server.sendmail(from_email, recipients, msg.as_string())
        else:
            with smtplib.SMTP(smtp_details['server'], smtp_details['port']) as server:
                server.starttls()
                server.login(smtp_details['username'], smtp_details['password'])
                server.sendmail(from_email, recipients, msg.as_string())
        logging.info("Alert email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send alert email: {e}")


def main() -> None:
    """Main function: checks token health and emails an alert on failure."""
    healthy, reason = check_token_health()

    if healthy:
        logging.info("Token is healthy.")
        return

    logging.error(f"Token health check failed: {reason}")

    config = load_config()
    smtp_details = {
        'server': config.get('SMTP', 'server'),
        'port': config.getint('SMTP', 'port'),
        'username': config.get('SMTP', 'username'),
        'password': config.get('SMTP', 'password'),
        'SSL': config.getboolean('SMTP', 'SSL'),
    }
    from_email = config.get('SMTP', 'from_email')
    recipients = [email.strip() for email in config.get('TokenHealth', 'recipients').split(',')]

    send_alert_email(reason, smtp_details, from_email, recipients)


if __name__ == '__main__':
    main()
