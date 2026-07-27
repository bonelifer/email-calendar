# Google Calendar Email Notifier and Summarizer

This project consists of Python scripts that interact with the Google Calendar API to fetch events and send email notifications or summaries, plus supporting scripts for reliability. The scripts are designed to be run as standalone tools, each serving a specific purpose:

1. **Daily Event Summary (`print.py`)**: Fetches today's events from all calendars and sends a formatted HTML email summary, optionally including a weather forecast.
2. **Event Notifier (`events.py`)**: Sends email notifications for events scheduled to occur after a specified number of days.
3. **Weekly Event Summary (`week-ahead.py`)**: Fetches events for the upcoming week and sends a formatted HTML email summary.
4. **Token Health Monitor (`token-health.py`)**: Checks whether the shared OAuth token can still be refreshed and emails an alert before it dies.
5. **Config Validator (`config-validator.py`)**: Validates `config.ini` sections/keys and `calendars.txt` entries before the other scripts run, so a typo fails loudly via email instead of silently breaking things under cron.

## Features

- **Daily Event Summary**:
  - Fetches all events for the current day across all calendars, merged and sorted chronologically.
  - Uses timezone-correct day boundaries and strictly re-checks each event's actual date, so all-day events dated for adjacent days can't leak into the wrong day's summary.
  - Formats event details in a clean HTML layout.
  - Optionally appends a weather forecast (via Open-Meteo, no API key required) — either a 12-hour hourly table (6 AM - 6 PM) or a simple daily high/low summary, controlled by the `simpleWeather` setting.
  - Sends the summary via email to configured recipients.

- **Event Notifier**:
  - Sends email notifications for events occurring after a specified number of days.
  - Supports multiple calendars, each with its own recipients, defined by name in `calendars.txt` and selected per run via `--calendar`.
  - Uses timezone-correct day boundaries and strictly re-checks each event's actual date, so all-day events dated for adjacent days can't leak into the wrong reminder.
  - Customizable email subject prefix.
  - SMTP configuration comes from `config.ini`; timezone and date format are read from `config.ini`, with an optional `--timezone` override.

- **Weekly Event Summary**:
  - Fetches all events for the upcoming week across all calendars, merged and sorted chronologically.
  - Strictly re-checks each event's actual date against the week's date range, so all-day events dated just outside the week can't leak into the summary.
  - Formats events in an HTML email with optional USA or non-USA date formats.
  - Sends the summary to multiple recipients.

- **Token Health Monitor**:
  - Attempts to refresh `token.json` and emails an alert only if it's missing, invalid, or fails to refresh.
  - Silent on success — designed to run daily via cron ahead of the other scripts.

- **Config Validator**:
  - Checks that all required `config.ini` sections and keys are present and well-formed (valid port, boolean, timezone, and recipient email values).
  - Validates the optional `[Weather]` section — valid booleans, and `latitude`/`longitude` present, numeric, and in valid geographic range whenever `enabled` is `True`.
  - Validates `calendars.txt` — every entry parses and has valid recipient emails, names are unique, and at least one entry exists.
  - Emails an alert listing the specific errors if validation fails, and exits non-zero.

## Requirements

- **Python 3.x**: The scripts are written in Python 3 and require a compatible version.
- **Google Calendar API Credentials**: You need to obtain `credentials.json` from the Google Developer Console.
- **SMTP Configuration**: The scripts use an SMTP server to send emails. Configuration is done via `config.ini`.
- **Internet access to `api.open-meteo.com`**: Only needed if the optional `[Weather]` feature in `print.py` is enabled.

## Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/yourusername/google-calendar-email-notifier.git
   cd google-calendar-email-notifier
   ```

2. **Install Dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

3. **Set Up Google Calendar API**:
   - Go to the [Google Developer Console](https://console.developers.google.com/).
   - Create a new project and enable the Google Calendar API.
   - Download the `credentials.json` file and place it in the project directory.
   - See Google's [Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python) for full walkthrough of project creation, OAuth consent screen setup, and generating `credentials.json`.

4. **Configure `config.ini`**:
   - Open `config.ini` and update the SMTP settings, timezone, and recipient emails as needed.

5. **Configure `calendars.txt`**:
   - Copy `calendars.txt.example` to `calendars.txt` and add one line per calendar you want `events.py` to be able to notify for (see [Configuration](#configuration)).

## Usage

### Daily Event Summary (`print.py`)

Run the script to fetch today's events and send an email summary:
```bash
python3 print.py
```

### Event Notifier (`events.py`)

Run the script with the desired arguments to send event notifications. The calendar ID and recipients are looked up by name from `calendars.txt`:
```bash
python3 events.py --calendar <name> --days-before <number_of_days> [--pre <prepend_subject>] [--timezone <timezone>]
```

- `--calendar` (required): Calendar name, as defined in `calendars.txt` (e.g. `Doctors`).
- `--days-before` (required): Number of days ahead to check for events.
- `--pre` (optional): Subject prefix; supports a `%days%` placeholder that gets replaced with the days-before value.
- `--timezone` (optional): Overrides the timezone from `config.ini` for this run.

To notify for multiple calendars, call `events.py` once per calendar (see `runcalendar.sh` for an example).

### Weekly Event Summary (`week-ahead.py`)

Run the script to fetch the upcoming week's events and send an email summary:
```bash
python3 week-ahead.py
```

### Token Health Monitor (`token-health.py`)

Run the script to check whether `token.json` can still be refreshed. Only sends an email if the check fails:
```bash
python3 token-health.py
```

Recommended to run daily via cron, before the other calendar scripts.

### Config Validator (`config-validator.py`)

Run the script to validate `config.ini` before the other scripts run:
```bash
python3 config-validator.py
```

Exits `0` if valid, `1` if validation fails (also emails an alert on failure). Recommended as the first step in `runcalendar.sh`.

## Configuration

The `config.ini` file contains all the necessary configuration settings:

- **[SMTP]**: SMTP server details for sending emails.
- **[Defaults]**: Default settings like timezone and date format.
- **[PrintCalendar]**: Recipients for the daily event summary.
- **[WeekAhead]**: Recipients for the weekly event summary.
- **[TokenHealth]**: Recipients for token health alert emails.
- **[Weather]** (optional): Enables and configures the weather forecast in the daily summary — `enabled`, `latitude`, `longitude`, and `simpleWeather` (hourly table vs. simple daily summary).

The `calendars.txt` file defines which calendars `events.py` can notify for, one per line:

```
calendar_id:<id>(<Name>); recipients:<email1>, <email2>
```

- `<id>`: The Google Calendar ID.
- `<Name>`: A short name used to select this calendar via `events.py --calendar <Name>`.
- `recipients`: Comma-separated list of emails to notify for this calendar.

Lines starting with `#` are treated as comments.

## Example `config.ini`

```ini
[SMTP]
server = smtp.gmail.com
port = 587
username = usr@gmail.com
password = PASSWORD
from_email = usr@gmail.com
SSL = False

[Defaults]
timezone = America/Chicago
print_usa_date = True

[PrintCalendar]
recipients = usr@gmail.com

[WeekAhead]
recipients = usr@gmail.com,usr2@gmail.com

[TokenHealth]
recipients = usr@gmail.com

[Weather]
enabled = True
latitude = 34.478
longitude = -93.0962
simpleWeather = False
```

## Example `calendars.txt`

```
calendar_id:calendarid1@group.calendar.google.com(Doctors); recipients:usr@gmail.com, usr2@gmail.com
calendar_id:calendarid2@group.calendar.google.com(Work); recipients:usr@gmail.com
```

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/email-calendar/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/email-calendar/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Google Calendar API for providing the calendar data.
- Python's `smtplib` and `email` libraries for handling email sending.
- `pytz` and `dateutil` for timezone and date handling.
- Code review, bug fixes, and documentation assisted by [Claude](https://www.anthropic.com/claude).

---

For any questions or issues, please open an issue on the GitHub repository.
