#!/usr/bin/env python3

"""
Google Calendar config.ini and calendars.txt Validator

Validates that config.ini contains all sections and keys required by
print.py, events.py, week-ahead.py, and token-health.py, and that key
values are well-formed (valid port/boolean/timezone, non-empty
recipients, etc.). Also validates the optional [Weather] section
(valid booleans, and latitude/longitude present and in range when
enabled), and calendars.txt (used by events.py to look up a calendar
ID and recipients by name).

Intended to be run first in runcalendar.sh, ahead of the other calendar
scripts, so a typo or missing key in config.ini or calendars.txt fails
loudly via email instead of silently breaking a downstream script under
cron.

Requirements:
- Python 3.x
- pytz
- SMTP configuration in config.ini (used to send the alert email itself,
  if the [SMTP] section is valid)

License:
    GNU General Public License v3.0
"""

import re
import smtplib
import configparser
import logging
from email.mime.text import MIMEText
import pytz

logging.basicConfig(level=logging.INFO)

CONFIG_FILE = "config.ini"
CALENDARS_FILE = "calendars.txt"

# Matches lines like:
#   calendar_id:6tudcbtak760hk0rmjbfnesqc0@group.calendar.google.com(Doctors); recipients:a@example.com, b@example.com
CALENDAR_LINE_RE = re.compile(
    r'^\s*calendar_id\s*:\s*(?P<calendar_id>[^()]+?)\s*\(\s*(?P<name>[^()]+?)\s*\)\s*;\s*'
    r'recipients\s*:\s*(?P<recipients>.+?)\s*$'
)

# section -> required keys
REQUIRED_SECTIONS: dict[str, list[str]] = {
    'SMTP': ['server', 'port', 'username', 'password', 'from_email', 'SSL'],
    'Defaults': ['timezone', 'print_usa_date'],
    'PrintCalendar': ['recipients'],
    'WeekAhead': ['recipients'],
    'TokenHealth': ['recipients'],
}

RECIPIENT_KEYS = ['recipients']


def validate_config(config: configparser.ConfigParser) -> list[str]:
    """
    Validates config.ini against REQUIRED_SECTIONS.

    Args:
        config (configparser.ConfigParser): Parsed config.ini.

    Returns:
        list[str]: A list of human-readable error messages. Empty if valid.
    """
    errors: list[str] = []

    for section, keys in REQUIRED_SECTIONS.items():
        if not config.has_section(section):
            errors.append(f"Missing section: [{section}]")
            continue

        for key in keys:
            if not config.has_option(section, key):
                errors.append(f"Missing key '{key}' in section [{section}]")
                continue

            value = config.get(section, key).strip()
            if not value:
                errors.append(f"Key '{key}' in section [{section}] is empty")
                continue

            if key in RECIPIENT_KEYS:
                emails = [email.strip() for email in value.split(',')]
                for email in emails:
                    if '@' not in email:
                        errors.append(f"Invalid email '{email}' in [{section}] '{key}'")

    # Type/format checks for keys with specific expected formats
    if config.has_section('SMTP') and config.has_option('SMTP', 'port'):
        try:
            config.getint('SMTP', 'port')
        except ValueError:
            errors.append("SMTP 'port' is not a valid integer")

    if config.has_section('SMTP') and config.has_option('SMTP', 'SSL'):
        try:
            config.getboolean('SMTP', 'SSL')
        except ValueError:
            errors.append("SMTP 'SSL' is not a valid boolean (use True/False)")

    if config.has_section('Defaults') and config.has_option('Defaults', 'print_usa_date'):
        try:
            config.getboolean('Defaults', 'print_usa_date')
        except ValueError:
            errors.append("Defaults 'print_usa_date' is not a valid boolean (use True/False)")

    if config.has_section('Defaults') and config.has_option('Defaults', 'timezone'):
        tz_value = config.get('Defaults', 'timezone').strip()
        if tz_value and tz_value not in pytz.all_timezones:
            errors.append(f"Defaults 'timezone' value '{tz_value}' is not a recognized pytz timezone")

    errors.extend(validate_weather_section(config))

    return errors


def validate_weather_section(config: configparser.ConfigParser) -> list[str]:
    """
    Validates the optional [Weather] section used by print.py.

    Checks 'enabled' and 'simpleWeather' are valid booleans if present, and
    that 'latitude'/'longitude' are present, numeric, and within valid
    geographic ranges whenever 'enabled' is True.

    Args:
        config (configparser.ConfigParser): Parsed config.ini.

    Returns:
        list[str]: A list of human-readable error messages. Empty if valid.
    """
    errors: list[str] = []

    if not config.has_section('Weather'):
        return errors  # Weather is an optional section entirely

    enabled = False
    if config.has_option('Weather', 'enabled'):
        try:
            enabled = config.getboolean('Weather', 'enabled')
        except ValueError:
            errors.append("Weather 'enabled' is not a valid boolean (use True/False)")
    else:
        errors.append("Missing key 'enabled' in section [Weather]")

    if config.has_option('Weather', 'simpleWeather'):
        try:
            config.getboolean('Weather', 'simpleWeather')
        except ValueError:
            errors.append("Weather 'simpleWeather' is not a valid boolean (use True/False)")

    if enabled:
        for key, value_range in (('latitude', (-90.0, 90.0)), ('longitude', (-180.0, 180.0))):
            if not config.has_option('Weather', key):
                errors.append(f"Missing key '{key}' in section [Weather] (required when 'enabled' is True)")
                continue

            raw_value = config.get('Weather', key).strip()
            if not raw_value:
                errors.append(f"Key '{key}' in section [Weather] is empty")
                continue

            try:
                numeric_value = float(raw_value)
            except ValueError:
                errors.append(f"Weather '{key}' value '{raw_value}' is not a valid number")
                continue

            low, high = value_range
            if not (low <= numeric_value <= high):
                errors.append(f"Weather '{key}' value '{numeric_value}' is out of valid range ({low} to {high})")

    return errors


def validate_calendars_file(calendars_file: str = CALENDARS_FILE) -> list[str]:
    """
    Validates the calendars.txt file used by events.py.

    Each non-comment, non-blank line must match:
        calendar_id:<id>(<Name>); recipients:<email1>, <email2>

    Args:
        calendars_file (str): Path to the calendars file.

    Returns:
        list[str]: A list of human-readable error messages. Empty if valid.
    """
    errors: list[str] = []

    try:
        with open(calendars_file, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return [f"{calendars_file} not found"]

    seen_names: set[str] = set()
    found_entry = False

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        match = CALENDAR_LINE_RE.match(line)
        if not match:
            errors.append(f"{calendars_file}:{line_number}: could not parse line: {line}")
            continue

        found_entry = True
        name = match.group('name').strip()
        if name in seen_names:
            errors.append(f"{calendars_file}:{line_number}: duplicate calendar name '{name}'")
        seen_names.add(name)

        emails = [email.strip() for email in match.group('recipients').split(',')]
        for email in emails:
            if not email or '@' not in email:
                errors.append(f"{calendars_file}:{line_number}: invalid email '{email}' for calendar '{name}'")

    if not found_entry:
        errors.append(f"{calendars_file} contains no valid calendar entries")

    return errors


def send_alert_email(errors: list[str], config: configparser.ConfigParser) -> None:
    """
    Sends an alert email listing config.ini errors, if the [SMTP] section
    and a TokenHealth/PrintCalendar recipient list are themselves valid
    enough to send with.
    """
    if not config.has_section('SMTP'):
        logging.error("Cannot send alert email: [SMTP] section missing or invalid.")
        return

    try:
        smtp_details = {
            'server': config.get('SMTP', 'server'),
            'port': config.getint('SMTP', 'port'),
            'username': config.get('SMTP', 'username'),
            'password': config.get('SMTP', 'password'),
            'SSL': config.getboolean('SMTP', 'SSL'),
        }
        from_email = config.get('SMTP', 'from_email')
    except Exception as e:
        logging.error(f"Cannot send alert email: [SMTP] section is malformed: {e}")
        return

    # Fall back through recipient lists in priority order, in case one
    # section's recipients key is the thing that's broken.
    recipients: list[str] = []
    for section in ('TokenHealth', 'PrintCalendar', 'WeekAhead'):
        if config.has_section(section) and config.has_option(section, 'recipients'):
            value = config.get(section, 'recipients').strip()
            if value:
                recipients = [email.strip() for email in value.split(',')]
                break

    if not recipients:
        logging.error("Cannot send alert email: no valid recipients found in any section.")
        return

    subject = "ALERT: config.ini validation failed"
    body = "config.ini failed validation with the following errors:\n\n" + "\n".join(f"- {e}" for e in errors)

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
    """Main function: validates config.ini and alerts on failure via email and exit code."""
    config = configparser.ConfigParser()

    try:
        read_files = config.read(CONFIG_FILE)
    except configparser.Error as e:
        logging.error(f"config.ini failed to parse: {e}")
        exit(1)

    if not read_files:
        logging.error(f"{CONFIG_FILE} not found or unreadable.")
        exit(1)

    errors = validate_config(config)
    errors.extend(validate_calendars_file())

    if not errors:
        logging.info("config.ini and calendars.txt are valid.")
        return

    logging.error("Configuration validation failed:")
    for error in errors:
        logging.error(f"  - {error}")

    send_alert_email(errors, config)
    exit(1)


if __name__ == '__main__':
    main()
