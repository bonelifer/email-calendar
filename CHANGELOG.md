# Changelog

All notable changes to this project are documented here.

## Initial Release — 2026-07-27 to 2026-07-28

### ✨ Features

- **Daily Event Summary** (`print.py`): Emails a same-day summary of events across all your calendars, merged and sorted chronologically, with an optional weather forecast.
- **Event Notifier** (`events.py`): Emails reminders for events coming up in a configurable number of days, with separate recipients per calendar.
- **Weekly Event Summary** (`week-ahead.py`): Emails a formatted summary of the upcoming week's events across all calendars.
- **Token Health Monitor** (`token-health.py`): Checks daily whether the shared Google OAuth token can still be refreshed, and emails an alert before it expires — silent when everything's fine.
- **Config Validator** (`config-validator.py`): Validates `config.ini` and `calendars.txt` before the other scripts run, so a typo or bad setting fails loudly via email instead of silently breaking a cron job.
- All calendar summaries correctly handle timezones and all-day events, so events dated for an adjacent day can't leak into the wrong summary or reminder.

## Links

- [GitHub Repository](https://github.com/bonelifer/email-calendar)
