#!/usr/bin/env python3

"""
Google Calendar Weekly Summary Email Script

This script fetches events from Google Calendar for the upcoming week
and sends them via email in a well-formatted HTML layout.

Features:
- Authenticates with Google Calendar API
- Retrieves events for the next 7 days
- Formats event details in HTML with USA or non-USA date format
- Sends the formatted events via email to configured recipients

Requirements:
- Python 3.x
- Google Calendar API credentials (credentials.json)
- SMTP configuration in config.ini

"""

import os
import datetime
import smtplib
import configparser
from email.mime.text import MIMEText
from typing import Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.discovery import Resource
import pytz
from dateutil import parser  # Import dateutil.parser for handling ISO date strings

# Load configuration settings from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# SMTP Configuration for sending emails
SMTP_SERVER = config['SMTP']['server']
SMTP_PORT = int(config['SMTP']['port'])
SMTP_USERNAME = config['SMTP']['username']
SMTP_PASSWORD = config['SMTP']['password']
FROM_EMAIL = config['SMTP']['from_email']
USE_SSL = config['SMTP'].getboolean('SSL')

# Default settings from config file
RECIPIENT_EMAILS = [email.strip() for email in config['WeekAhead']['recipients'].split(',')]
TIMEZONE = pytz.timezone(config['Defaults']['timezone'])
USE_USA_DATE_FORMAT = config['Defaults'].getboolean('print_usa_date', fallback=False)

# Google Calendar API Scope (Read-only)
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']


def get_google_calendar_service() -> Resource:
    """
    Authenticates and returns a Google Calendar service object, handling
    token issues gracefully.

    If a token exists, it refreshes or re-authenticates as needed.
    Otherwise, it requests user authentication via OAuth.

    Returns:
        Resource: Google Calendar API service instance.
    """
    creds = None
    token_file = 'token.json'

    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            print(f"Error loading {token_file}: {e}. Deleting and retrying authentication.")
            os.remove(token_file)  # Remove corrupt token

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}. Deleting {token_file} and re-authenticating.")
                os.remove(token_file)  # Remove invalid token
                creds = None  # Force new authentication flow

        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            except FileNotFoundError:
                print("ERROR: credentials.json not found! Cannot authenticate.")
                exit(1)

        with open(token_file, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def _event_sort_key(event: dict[str, Any]) -> str:
    """
    Returns a sortable string key for an event's start, so events pulled
    from multiple calendars can be merged into one chronological order.

    Args:
        event (dict[str, Any]): Event details from the Google Calendar API.

    Returns:
        str: ISO 8601 dateTime or date string used for sorting.
    """
    return event['start'].get('dateTime', event['start'].get('date', ''))


def get_next_weeks_events(service: Resource) -> list[dict[str, Any]]:
    """
    Fetches all Google Calendar events for the next week from all available calendars.

    Args:
        service (Resource): Google Calendar API service instance.

    Returns:
        list[dict[str, Any]]: A list of event objects for the next week,
        merged across all calendars and sorted chronologically by start time.
    """
    now = datetime.datetime.now(TIMEZONE)

    # Calculate the start and end of next week (Monday to Sunday)
    start_of_next_week = now + datetime.timedelta(days=(7 - now.weekday()))
    start_of_next_week = start_of_next_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_next_week = start_of_next_week + datetime.timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)

    events: list[dict[str, Any]] = []

    calendars: list[dict[str, Any]] = []
    page_token = None
    try:
        while True:
            calendar_list_result = service.calendarList().list(pageToken=page_token).execute()
            calendars.extend(calendar_list_result.get('items', []))
            page_token = calendar_list_result.get('nextPageToken')
            if not page_token:
                break
    except HttpError as e:
        print(f"Failed to retrieve calendar list: {e}")
        return events

    for cal in calendars:
        try:
            page_token = None
            while True:
                events_result = service.events().list(
                    calendarId=cal['id'],
                    timeMin=start_of_next_week.isoformat(),
                    timeMax=end_of_next_week.isoformat(),
                    singleEvents=True,
                    orderBy='startTime',
                    pageToken=page_token
                ).execute()
                events.extend(events_result.get('items', []))
                page_token = events_result.get('nextPageToken')
                if not page_token:
                    break
        except HttpError as e:
            print(f"Failed to retrieve events for calendar '{cal.get('id')}': {e}")
            continue

    return sorted(_filter_to_range(events, start_of_next_week.date(), end_of_next_week.date()), key=_event_sort_key)


def _filter_to_range(events: list[dict[str, Any]], range_start: datetime.date, range_end: datetime.date) -> list[dict[str, Any]]:
    """
    Strictly filters events to those actually falling within [range_start, range_end].

    Google's timeMin/timeMax query treats all-day events' boundaries as UTC
    midnight rather than the configured local timezone, so an all-day event
    dated just outside the requested week can still be returned by the API
    as overlapping the window in timezones behind UTC. This re-checks each
    event's real date (all-day) or localized start date (timed) and drops
    anything that doesn't actually fall within the range.

    Args:
        events (list[dict[str, Any]]): Events returned by the Calendar API.
        range_start (datetime.date): First day of the requested week (inclusive).
        range_end (datetime.date): Last day of the requested week (inclusive).

    Returns:
        list[dict[str, Any]]: Events confirmed to fall within the range.
    """
    filtered: list[dict[str, Any]] = []
    for event in events:
        start = event['start']
        if 'date' in start:
            event_date = datetime.date.fromisoformat(start['date'])
        else:
            event_date = parser.parse(start['dateTime']).astimezone(TIMEZONE).date()

        if range_start <= event_date <= range_end:
            filtered.append(event)

    return filtered


def format_event(event: dict[str, Any]) -> str:
    """
    Formats a Google Calendar event as an HTML string.

    Args:
        event (dict[str, Any]): Event details from the Google Calendar API.

    Returns:
        str: Formatted HTML representation of the event.
    """
    start = event['start'].get('dateTime', event['start'].get('date'))
    end = event['end'].get('dateTime', event['end'].get('date'))
    summary = event.get('summary', 'No Title')
    location = event.get('location', 'No Location')
    calendar_name = event.get('organizer', {}).get('displayName', 'Unknown Calendar')

    # Handle all-day events
    if 'date' in event['start']:
        start_date = parser.parse(start).date()
        end_date = parser.parse(end).date()

        date_format = '%m/%d/%Y' if USE_USA_DATE_FORMAT else '%d/%m/%Y'
        start_date_str = start_date.strftime(date_format)
        end_date_str = end_date.strftime(date_format)

        return f"<p><strong>{summary}</strong> (All Day)<br><strong>Calendar:</strong> {calendar_name}<br><strong>Location:</strong> {location}<br><strong>From:</strong> {start_date_str} <strong>To:</strong> {end_date_str}</p>"

    else:
        start_time = parser.parse(start).astimezone(TIMEZONE).strftime('%I:%M %p')
        end_time = parser.parse(end).astimezone(TIMEZONE).strftime('%I:%M %p')
        event_date = parser.parse(start).astimezone(TIMEZONE).strftime('%m/%d/%Y' if USE_USA_DATE_FORMAT else '%d/%m/%Y')

        return f"<p><strong>{summary}</strong> ({start_time} - {end_time})<br><strong>Calendar:</strong> {calendar_name}<br><strong>Location:</strong> {location}<br><strong>Date:</strong> {event_date}</p>"


def create_html_email(events: list[dict[str, Any]]) -> str:
    """
    Generates an HTML-formatted email containing calendar events.

    Args:
        events (list[dict[str, Any]]): List of Google Calendar event dictionaries.

    Returns:
        str: HTML email content.
    """
    body_content = "<p>No events next week.</p>" if not events else "".join(format_event(event) for event in events)

    printed_time = datetime.datetime.now(TIMEZONE).strftime('%m/%d/%Y %I:%M:%S %p' if USE_USA_DATE_FORMAT else '%Y-%m-%d %H:%M:%S')
    header_date = datetime.datetime.now(TIMEZONE).strftime('%A, %B %d, %Y' if USE_USA_DATE_FORMAT else '%A, %d %B %Y')

    return f"""<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html;charset=UTF-8">
        <style>
            @media print {{
                body {{ font-family: monospace; width: 181mm !important; overflow-wrap: break-word; }}
            }}
        </style>
    </head>
    <body>
        <h1 style="border: solid black 8px; background-color: black; color: white; padding: 5px;">
            Google Calendar<br> {header_date}
        </h1>
        {body_content}
        <p style="border: solid black 8px; background-color: black; color: white; padding: 5px;">
            Printed {printed_time}
        </p>
    </body>
    </html>"""


def send_email(subject: str, body: str, to_emails: list[str]) -> None:
    """
    Sends an email with the given subject and HTML content.

    Args:
        subject (str): Email subject line.
        body (str): HTML email content.
        to_emails (list[str]): List of recipient email addresses.
    """
    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From'] = FROM_EMAIL
    msg['To'] = ', '.join(to_emails)

    try:
        with (smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) if USE_SSL else smtplib.SMTP(SMTP_SERVER, SMTP_PORT)) as server:
            if not USE_SSL:
                server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_emails, msg.as_string())
        print("Email sent successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")


def main() -> None:
    """Main function to fetch calendar events and send them via email."""
    service = get_google_calendar_service()
    events = get_next_weeks_events(service)
    send_email("Next Week's Calendar Events", create_html_email(events), RECIPIENT_EMAILS)


if __name__ == '__main__':
    main()
