#!/usr/bin/bash
set -e

# Resolve the script's own directory so relative paths (config.ini,
# calendars.txt, credentials.json, token.json) work regardless of the
# caller's working directory, e.g. under cron.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

python config-validator.py
python token-health.py
python events.py --calendar Default --days-before 12 --pre "Schedule Apointment with SCAT: "
python events.py --calendar Default --days-before 2 --pre "Appointment in %days% days: "
#python events.py --calendar Default --days-before 3 --pre "Appointment in %days% days: "
python events.py --calendar Default --days-before 1 --pre "Appointment in %days% days: "
python events.py --calendar Default --days-before 0 --pre "You have an appointment today: "
python print.py

# Check if today is Sunday
if [ "$(date +%A)" == "Sunday" ]; then
    echo "Today is Sunday, running command..."
    python week-ahead.py
else
    echo "Today is not Sunday..."
fi
