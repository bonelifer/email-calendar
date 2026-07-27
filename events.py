#!/usr/bin/env python3

"""
Google Calendar Event Email Reminder Script

This script retrieves upcoming events from a specified Google Calendar
and sends email reminders based on a configurable number of days in advance.

Features:
- Uses `token.json` for authentication (no more `token.pickle`).
- Sends email reminders using SMTP (supports SSL and TLS).
- Configurable via `config.ini` (SMTP settings, timezone, date format) and
  `calendars.txt` (calendar ID and recipients, selected by name via
  `--calendar`).
- Includes error handling and retries for email failures.

Requirements:
- Google API client (`google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `google-api-python-client`)
- `pytz`, `dateutil`, and `beautifulsoup4` for date/time and HTML parsing.

Usage:
    python3 events.py --calendar Doctors --days-before 3 --pre "Upcoming Event: "

License:
    GNU General Public License v3.0
"""

import datetime
import argparse
import os
import re
import smtplib
import logging
import configparser
from email.mime.text import MIMEText
from typing import Any
from googleapiclient.discovery import build, Resource
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
import pytz
from dateutil import parser
import time
import random
from bs4 import BeautifulSoup  # For stripping HTML from event descriptions

# Google API Scope for reading calendar events
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
TOKEN_FILE = "token.json"  # Authentication token storage
CALENDARS_FILE = "calendars.txt"  # Calendar name -> ID/recipients lookup

# Matches lines like:
#   calendar_id:6tudcbtak760hk0rmjbfnesqc0@group.calendar.google.com(Doctors); recipients:a@example.com, b@example.com
CALENDAR_LINE_RE = re.compile(
    r'^\s*calendar_id\s*:\s*(?P<calendar_id>[^()]+?)\s*\(\s*(?P<name>[^()]+?)\s*\)\s*;\s*'
    r'recipients\s*:\s*(?P<recipients>.+?)\s*$'
)


def load_config(config_file: str = "config.ini") -> configparser.ConfigParser:
    """Loads SMTP and calendar configuration from config.ini."""
    config = configparser.ConfigParser()
    config.read(config_file)
    return config


def load_calendars(calendars_file: str = CALENDARS_FILE) -> dict[str, dict[str, Any]]:
    """
    Loads calendar definitions from a plain-text calendars file.

    Each non-comment, non-blank line has the form:
        calendar_id:<id>(<Name>); recipients:<email1>, <email2>

    Args:
        calendars_file (str): Path to the calendars file.

    Returns:
        dict[str, dict[str, Any]]: Maps calendar name to
        {'calendar_id': str, 'recipients': list[str]}.
    """
    calendars: dict[str, dict[str, Any]] = {}

    with open(calendars_file, 'r') as f:
        for line_number, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue

            match = CALENDAR_LINE_RE.match(line)
            if not match:
                logging.warning(f"{calendars_file}:{line_number}: could not parse line, skipping: {line}")
                continue

            name = match.group('name').strip()
            calendars[name] = {
                'calendar_id': match.group('calendar_id').strip(),
                'recipients': [email.strip() for email in match.group('recipients').split(',')],
            }

    return calendars


def strip_html(html_content: str) -> str:
    """Removes HTML tags from a given string."""
    return BeautifulSoup(html_content, "html.parser").get_text(separator="\n")


def get_calendar_service() -> Resource:
    """Authenticates and returns a Google Calendar API service object, handling token issues gracefully."""
    creds = None

    # Load credentials from token.json if it exists
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logging.error(f"Error loading {TOKEN_FILE}: {e}. Deleting and retrying authentication.")
            os.remove(TOKEN_FILE)

    # If credentials are invalid, refresh or request new authentication
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logging.error(f"Token refresh failed: {e}. Deleting {TOKEN_FILE} and re-authenticating.")
                os.remove(TOKEN_FILE)
                creds = None

        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            except FileNotFoundError:
                logging.error("credentials.json not found! Cannot authenticate.")
                exit(1)

            # Save new credentials to token.json
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def get_upcoming_events(service: Resource, calendar_id: str, days_before: int, timezone: str) -> list[dict[str, Any]]:
    """Fetches events from Google Calendar for a specified future date, in the configured timezone."""
    tz = pytz.timezone(timezone)
    target_date = (datetime.datetime.now(tz) + datetime.timedelta(days=days_before)).date()

    # Define the start and end of the target day, localized to the configured timezone
    start_of_day = tz.localize(datetime.datetime.combine(target_date, datetime.time.min))
    end_of_day = tz.localize(datetime.datetime.combine(target_date, datetime.time.max))

    logging.info(f"Fetching events for: {target_date}")

    events: list[dict[str, Any]] = []
    page_token = None
    try:
        while True:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy='startTime',
                pageToken=page_token,
            ).execute()
            events.extend(events_result.get('items', []))
            page_token = events_result.get('nextPageToken')
            if not page_token:
                break
        return _filter_to_date(events, target_date, tz)
    except HttpError as e:
        logging.error(f"Google Calendar API error: {e}")
        return []


def _filter_to_date(events: list[dict[str, Any]], target_date: datetime.date, tz: datetime.tzinfo) -> list[dict[str, Any]]:
    """
    Strictly filters events to those actually falling on target_date.

    Google's timeMin/timeMax query treats all-day events' boundaries as UTC
    midnight rather than the configured local timezone, so an all-day event
    dated for the next calendar day can still be returned by the API as
    overlapping "today" in timezones behind UTC. This re-checks each event's
    real date (all-day) or localized start date (timed) and drops anything
    that doesn't actually match.

    Args:
        events (list[dict[str, Any]]): Events returned by the Calendar API.
        target_date (datetime.date): The calendar day events must fall on.
        tz (datetime.tzinfo): Timezone to localize timed events into before comparing.

    Returns:
        list[dict[str, Any]]: Events confirmed to fall on target_date.
    """
    filtered: list[dict[str, Any]] = []
    for event in events:
        start = event.get('start', {})
        if 'date' in start:
            event_date = datetime.date.fromisoformat(start['date'])
        elif 'dateTime' in start:
            event_date = parser.parse(start['dateTime']).astimezone(tz).date()
        else:
            continue

        if event_date == target_date:
            filtered.append(event)

    return filtered


def format_event_datetime(event: dict[str, Any], timezone: str = 'America/Chicago', use_usa_date_format: bool = False) -> tuple[str, str]:
    """Formats event start time into a human-readable string."""
    event_start = event.get('start', {})

    if 'dateTime' in event_start:
        dt = parser.parse(event_start['dateTime']).astimezone(pytz.timezone(timezone))
        date_format = '%m/%d/%Y' if use_usa_date_format else '%d/%m/%Y'
        return dt.strftime(date_format), dt.strftime("%I:%M %p %Z")
    elif 'date' in event_start:
        dt = parser.parse(event_start['date'])
        date_format = '%m/%d/%Y' if use_usa_date_format else '%d/%m/%Y'
        return dt.strftime(date_format), "All Day"
    else:
        return "Unknown Date", "Unknown Time"


def create_email_body(event: dict[str, Any], timezone: str, use_usa_date_format: bool) -> str:
    """Generates email content from event details."""
    date_str, time_str = format_event_datetime(event, timezone=timezone, use_usa_date_format=use_usa_date_format)
    event_description = event.get('description', 'No Description')
    event_location = event.get('location', 'No Location')

    return f"Date: {date_str}\nStart Time: {time_str}\nLocation: {event_location}\n\n{strip_html(event_description)}"


def send_email(recipient: str, subject: str, body: str, smtp_details: dict[str, Any], from_email: str) -> None:
    """Sends an email notification. Raises on failure so callers can retry."""
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = recipient

    if smtp_details['SSL']:
        with smtplib.SMTP_SSL(smtp_details['server'], smtp_details['port']) as server:
            server.login(smtp_details['username'], smtp_details['password'])
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_details['server'], smtp_details['port']) as server:
            server.starttls()
            server.login(smtp_details['username'], smtp_details['password'])
            server.send_message(msg)

    logging.info(f"Email sent successfully to {recipient}")


def process_events(
    events: list[dict[str, Any]],
    recipients: list[str],
    days_before: int,
    prepend_subject: str,
    timezone: str,
    use_usa_date_format: bool,
    smtp_details: dict[str, Any],
    from_email: str,
) -> None:
    """Processes events and sends email notifications, retrying once on failure."""
    for event in events:
        event_title = event.get('summary', 'No Title')
        body = create_email_body(event, timezone, use_usa_date_format)
        subject = f"{event_title} in {days_before} days"

        if prepend_subject:
            subject = f"{prepend_subject.replace('%days%', str(days_before))} {subject}"

        for recipient in recipients:
            try:
                send_email(recipient, subject, body, smtp_details, from_email)
            except smtplib.SMTPException as e:
                logging.error(f"Failed to send email to {recipient}: {e}. Retrying after failure...")
                time.sleep(random.uniform(2, 5))  # Random delay before retry
                try:
                    send_email(recipient, subject, body, smtp_details, from_email)
                except smtplib.SMTPException as retry_e:
                    logging.error(f"Retry failed for {recipient}: {retry_e}")


def main() -> None:
    """Main function: Parses arguments, loads config, fetches events, and sends emails."""
    arg_parser = argparse.ArgumentParser(description="Send email reminders for Google Calendar events.")
    arg_parser.add_argument('--calendar', type=str, required=True, help='Calendar name to use, as defined in calendars.txt (e.g. Doctors)')
    arg_parser.add_argument('--days-before', type=int, required=True, help='Days before event to notify')
    arg_parser.add_argument('--pre', type=str, help='Optional subject prefix with %%days%% placeholder.')
    arg_parser.add_argument('--timezone', type=str, default=None, help='Timezone for event times (overrides config.ini)')
    args = arg_parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    try:
        config = load_config()
        calendars = load_calendars()
        if args.calendar not in calendars:
            available = ', '.join(calendars) or 'none'
            logging.error(f"Calendar '{args.calendar}' not found in {CALENDARS_FILE}. Available: {available}")
            exit(1)

        calendar_id = calendars[args.calendar]['calendar_id']
        recipients = calendars[args.calendar]['recipients']

        timezone = args.timezone or config.get('Defaults', 'timezone', fallback='America/Chicago')
        use_usa_date_format = config.getboolean('Defaults', 'print_usa_date', fallback=False)

        smtp_details = {
            'server': config.get('SMTP', 'server'),
            'port': config.getint('SMTP', 'port'),
            'username': config.get('SMTP', 'username'),
            'password': config.get('SMTP', 'password'),
            'SSL': config.getboolean('SMTP', 'SSL'),
        }
        from_email = config.get('SMTP', 'from_email')

        service = get_calendar_service()
        events = get_upcoming_events(service, calendar_id, args.days_before, timezone)

        process_events(events, recipients, args.days_before, args.pre, timezone, use_usa_date_format, smtp_details, from_email)

    except Exception as e:
        logging.exception(f"A critical error occurred: {e}")


if __name__ == '__main__':
    main()
