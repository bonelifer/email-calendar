#!/usr/bin/env python3

"""
Google Calendar Daily Events Email Script

This script fetches today's events from Google Calendar and sends them
via email in a well-formatted HTML layout.

Features:
- Authenticates with Google Calendar API
- Retrieves events for the current day
- Formats event details in HTML
- Optionally appends a weather forecast (via Open-Meteo, no API key required):
  either a 12-hour hourly table (6 AM - 6 PM) or a simple daily high/low
  summary, controlled by the 'simpleWeather' config.ini setting
- Sends the formatted events via email to configured recipients

Requirements:
- Python 3.x
- Google Calendar API credentials (credentials.json)
- SMTP configuration in config.ini
- Optional: [Weather] section in config.ini (enabled, latitude, longitude,
  simpleWeather) to include a forecast

"""

import os
import datetime
import smtplib
import configparser
from email.mime.text import MIMEText
from typing import Any, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError
import pytz
import requests

# Load configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# SMTP Configuration
SMTP_SERVER = config['SMTP']['server']
SMTP_PORT = int(config['SMTP']['port'])
SMTP_USERNAME = config['SMTP']['username']
SMTP_PASSWORD = config['SMTP']['password']
FROM_EMAIL = config['SMTP']['from_email']
USE_SSL = config['SMTP'].getboolean('SSL')

# Defaults
RECIPIENT_EMAILS = [email.strip() for email in config['PrintCalendar']['recipients'].split(',')]
TIMEZONE = pytz.timezone(config['Defaults']['timezone'])
USE_USA_DATE_FORMAT = config['Defaults'].getboolean('print_usa_date', fallback=False)

# Weather (optional)
WEATHER_ENABLED = config.getboolean('Weather', 'enabled', fallback=False) if config.has_section('Weather') else False
WEATHER_LATITUDE = config.get('Weather', 'latitude', fallback=None) if config.has_section('Weather') else None
WEATHER_LONGITUDE = config.get('Weather', 'longitude', fallback=None) if config.has_section('Weather') else None
WEATHER_SIMPLE = config.getboolean('Weather', 'simpleWeather', fallback=False) if config.has_section('Weather') else False

# Google Calendar API Scope (Read-only)
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# WMO weather codes -> short description (subset covering common conditions)
WEATHER_CODE_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def get_google_calendar_service() -> Resource:
    """Returns an authenticated Google Calendar service object, handling token issues gracefully."""
    creds = None
    token_file = 'token.json'

    # Check if token.json exists
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            print(f"Error loading token.json: {e}. Deleting and retrying authentication.")
            os.remove(token_file)  # Remove corrupt token

    # If no valid credentials are found, authenticate again
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}. Deleting token.json and re-authenticating.")
                os.remove(token_file)  # Remove invalid token
                creds = None  # Force new authentication flow

        if not creds:
            # Run OAuth flow to get new credentials
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                # Save new credentials
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
            except FileNotFoundError:
                print("ERROR: credentials.json not found! Cannot authenticate.")
                exit(1)

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


def get_todays_events(service: Resource, target_date: datetime.date) -> list[dict[str, Any]]:
    """
    Fetches all events for the given day from all calendars, merged
    and sorted chronologically by start time.

    Args:
        service (Resource): Google Calendar API service instance.
        target_date (datetime.date): The calendar day to fetch events for.

    Returns:
        list[dict[str, Any]]: The day's events across all calendars, sorted by start time.
    """
    start_of_day = TIMEZONE.localize(datetime.datetime.combine(target_date, datetime.time.min)).isoformat()
    end_of_day = TIMEZONE.localize(datetime.datetime.combine(target_date, datetime.time.max)).isoformat()

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
                    timeMin=start_of_day,
                    timeMax=end_of_day,
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

    return sorted(_filter_to_date(events, target_date), key=_event_sort_key)


def _filter_to_date(events: list[dict[str, Any]], target_date: datetime.date) -> list[dict[str, Any]]:
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

    Returns:
        list[dict[str, Any]]: Events confirmed to fall on target_date.
    """
    filtered: list[dict[str, Any]] = []
    for event in events:
        start = event['start']
        if 'date' in start:
            event_date = datetime.date.fromisoformat(start['date'])
        else:
            event_date = datetime.datetime.fromisoformat(start['dateTime']).astimezone(TIMEZONE).date()

        if event_date == target_date:
            filtered.append(event)

    return filtered


def format_event(event: dict[str, Any]) -> str:
    """Formats an event into an HTML-friendly string."""
    start = event['start'].get('dateTime', event['start'].get('date'))
    end = event['end'].get('dateTime', event['end'].get('date'))
    summary = event.get('summary', 'No Title')
    location = event.get('location', 'No Location')
    calendar_name = event.get('organizer', {}).get('displayName', 'Unknown Calendar')

    if 'date' in event['start']:  # All-day event
        return f"<p><strong>{summary}</strong> (All Day)<br><strong>Calendar:</strong> {calendar_name}<br><strong>Location:</strong> {location}</p>"

    else:  # Timed event
        # Ensure ISO 8601 format compatibility
        start_dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(TIMEZONE)
        end_dt = datetime.datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(TIMEZONE)

        start_time = start_dt.strftime('%I:%M %p')
        end_time = end_dt.strftime('%I:%M %p')

        return f"<p><strong>{summary}</strong> ({start_time} - {end_time})<br><strong>Calendar:</strong> {calendar_name}<br><strong>Location:</strong> {location}</p>"


def get_hourly_forecast(latitude: str, longitude: str, target_date: datetime.date) -> Optional[list[dict[str, Any]]]:
    """
    Fetches a 12-hour hourly forecast, 6 AM - 6 PM, for the given day from
    the Open-Meteo API (no API key required).

    Args:
        latitude (str): Forecast location latitude.
        longitude (str): Forecast location longitude.
        target_date (datetime.date): The calendar day to fetch the forecast for.

    Returns:
        Optional[list[dict[str, Any]]]: List of hourly forecast dicts, each with
        'time', 'temperature', 'precipitation_probability', and 'description'
        keys, ordered 6 AM through 5 PM (12 hours). None if the forecast
        could not be fetched.
    """
    temperature_unit = "fahrenheit" if USE_USA_DATE_FORMAT else "celsius"
    date_str = target_date.isoformat()

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,precipitation_probability,weathercode",
                "temperature_unit": temperature_unit,
                "timezone": str(TIMEZONE),
                "start_date": date_str,
                "end_date": date_str,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        hourly = data["hourly"]

        forecast: list[dict[str, Any]] = []
        for i, time_str in enumerate(hourly["time"]):
            hour = datetime.datetime.fromisoformat(time_str).hour
            if 6 <= hour < 18:  # 6 AM through 5 PM = 12 hours
                forecast.append({
                    "time": datetime.datetime.fromisoformat(time_str),
                    "temperature": hourly["temperature_2m"][i],
                    "precipitation_probability": hourly["precipitation_probability"][i],
                    "description": WEATHER_CODE_DESCRIPTIONS.get(hourly["weathercode"][i], "Unknown conditions"),
                    "unit": "°F" if USE_USA_DATE_FORMAT else "°C",
                })

        return forecast if forecast else None
    except Exception as e:
        print(f"Failed to fetch weather forecast: {e}")
        return None


def format_hourly_forecast(forecast: list[dict[str, Any]]) -> str:
    """Formats an hourly forecast list into an HTML table."""
    rows = "".join(
        f"<tr><td>{hour['time'].strftime('%I %p')}</td>"
        f"<td>{hour['temperature']}{hour['unit']}</td>"
        f"<td>{hour['description']}</td>"
        f"<td>{hour['precipitation_probability']}%</td></tr>"
        for hour in forecast
    )

    return f"""<p><strong>Today's Forecast (6 AM - 6 PM):</strong></p>
    <table style="border-collapse: collapse;" border="1" cellpadding="4">
        <tr><th>Time</th><th>Temp</th><th>Conditions</th><th>Precip</th></tr>
        {rows}
    </table>"""


def get_simple_forecast(latitude: str, longitude: str, target_date: datetime.date) -> Optional[dict[str, Any]]:
    """
    Fetches a single daily high/low forecast summary for the given day from
    the Open-Meteo API (no API key required).

    Args:
        latitude (str): Forecast location latitude.
        longitude (str): Forecast location longitude.
        target_date (datetime.date): The calendar day to fetch the forecast for.

    Returns:
        Optional[dict[str, Any]]: Dict with 'high', 'low', 'precipitation_probability',
        'description', and 'unit' keys, or None if the forecast could not be fetched.
    """
    temperature_unit = "fahrenheit" if USE_USA_DATE_FORMAT else "celsius"
    date_str = target_date.isoformat()

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                "temperature_unit": temperature_unit,
                "timezone": str(TIMEZONE),
                "start_date": date_str,
                "end_date": date_str,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        daily = data["daily"]

        return {
            "high": daily["temperature_2m_max"][0],
            "low": daily["temperature_2m_min"][0],
            "precipitation_probability": daily["precipitation_probability_max"][0],
            "description": WEATHER_CODE_DESCRIPTIONS.get(daily["weathercode"][0], "Unknown conditions"),
            "unit": "°F" if USE_USA_DATE_FORMAT else "°C",
        }
    except Exception as e:
        print(f"Failed to fetch weather forecast: {e}")
        return None


def format_simple_forecast(forecast: dict[str, Any]) -> str:
    """Formats a simple daily forecast dict into an HTML-friendly string."""
    return (
        f"<p><strong>Today's Forecast:</strong> {forecast['description']}, "
        f"{forecast['low']}{forecast['unit']} - {forecast['high']}{forecast['unit']}, "
        f"{forecast['precipitation_probability']}% chance of precipitation</p>"
    )


def create_html_email(events: list[dict[str, Any]], forecast_html: str = "") -> str:
    """Creates an HTML email body styled for printing."""
    if not events:
        body_content = "<p>Nothing on any of your calendars today.</p>"
    else:
        body_content = "".join(format_event(event) for event in events)

    printed_time = datetime.datetime.now(TIMEZONE).strftime('%m/%d/%Y %I:%M:%S %p' if USE_USA_DATE_FORMAT else '%Y-%m-%d %H:%M:%S')
    header_date = datetime.datetime.now(TIMEZONE).strftime('%A, %B %d, %Y' if USE_USA_DATE_FORMAT else '%A, %d %B %Y')

    return f"""<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html;charset=UTF-8">
        <style>
            @media print {{ body {{ font-family: monospace; width: 181mm !important; }} }}
        </style>
    </head>
    <body>
        <h1 style="border: solid black 8px; background-color: black; color: white; padding: 5px;">
            Google Calendar<br> {header_date}
        </h1>
        {body_content}
        {forecast_html}
        <p style="border: solid black 8px; background-color: black; color: white; padding: 5px;">
            Printed {printed_time}
        </p>
    </body>
    </html>"""


def send_email(subject: str, body: str, to_emails: list[str]) -> None:
    """Sends an email with the given subject and HTML body to multiple recipients."""
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
    """Main function to fetch today's calendar events and send them via email."""
    target_date = datetime.datetime.now(TIMEZONE).date()

    service = get_google_calendar_service()
    events = get_todays_events(service, target_date)

    forecast_html = ""
    if WEATHER_ENABLED and WEATHER_LATITUDE and WEATHER_LONGITUDE:
        if WEATHER_SIMPLE:
            simple_forecast = get_simple_forecast(WEATHER_LATITUDE, WEATHER_LONGITUDE, target_date)
            forecast_html = format_simple_forecast(simple_forecast) if simple_forecast else ""
        else:
            hourly_forecast = get_hourly_forecast(WEATHER_LATITUDE, WEATHER_LONGITUDE, target_date)
            forecast_html = format_hourly_forecast(hourly_forecast) if hourly_forecast else ""

    send_email("Today's Calendar Events", create_html_email(events, forecast_html), RECIPIENT_EMAILS)


if __name__ == '__main__':
    main()
