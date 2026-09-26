#!/bin/bash

# Define the cron file path in a writable location
CRON_FILE="/var/spool/cron/crontabs/root"

# Ensure cron log file exists
touch /var/log/cron.log
chmod 666 /var/log/cron.log

# Ensure cron directory has correct permissions
chmod -R 777 /var/spool/cron/crontabs

# Remove existing crontab to avoid duplicate jobs
rm -f "$CRON_FILE"

# Add the default cron jobs
# NOTE: the scripts are shipped as plain .py sources under /data/overlayFiles/
# (see Dockerfile) and run directly with python3 - a prior PyInstaller
# compile step was removed because its bootloaders don't run on this
# infrastructure (see Dockerfile comment for details).
# Summary digest schedules are cron "MIN HOUR" pairs taken from env so they can
# be changed from the bod-env-details ConfigMap without a rebuild. The telnet
# digest defaults to 08:45 so it lands before the per-port alert window
# (ALERT_WINDOW_START, default 09:00) opens.
URL_SUMMARY_CRON="${URL_SUMMARY_CRON:-00 12}"
TELNET_SUMMARY_CRON="${TELNET_SUMMARY_CRON:-45 8}"

echo "*/2 * * * *  /usr/bin/python3 /data/overlayFiles/urlchecker.py  > /tmp/urlchecker.log 2>&1" >> "$CRON_FILE"
echo "00 07 * * *  /usr/bin/python3 /data/overlayFiles/holiday_checker.py  > /tmp/holiday_checker.log 2>&1" >> "$CRON_FILE"
echo "${URL_SUMMARY_CRON} * * * /usr/bin/python3 /data/overlayFiles/urlchecker.py --summary > /tmp/urlchecker_1.log 2>&1" >> "$CRON_FILE"
echo "${TELNET_SUMMARY_CRON} * * * /usr/bin/python3 /data/overlayFiles/telnet_checker.py --summary > /tmp/telnet_checker1.log 2>&1" >> "$CRON_FILE"

# Conditionally add k8s_ssl_check cron job
if [ "${K8S_ENABLED:-no}" = "yes" ]; then
    echo "00 07 * * *  /usr/bin/python3 /data/overlayFiles/k8s_ssl_check.py  > /tmp/k8s_ssl_check.log 2>&1" >> "$CRON_FILE"
fi

# Conditionally add telnet_checker cron job
if [ "${TELNET_ENABLED:-no}" = "yes" ]; then
    echo "*/2 * * * *  /usr/bin/python3 /data/overlayFiles/telnet_checker.py  > /tmp/telnet_checker.log 2>&1" >> "$CRON_FILE"
fi

# Ensure proper permissions for the crontab file
chmod 600 "$CRON_FILE"
chown root:crontab "$CRON_FILE"

# Load the cron jobs
crontab "$CRON_FILE"

# Start the cron service in the background
cron -L 15

# Keep the container running and log output
tail -f /var/log/cron.log

