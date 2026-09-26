import yaml
import uuid
import os
import socket
import telnetlib
import time
import requests
import argparse
from datetime import datetime
from FinspotUtils import Redis, RMQ  # Ensure these are available in your environment

HostName = socket.gethostname().split('.')[0]
SiteName = os.getenv('SITE_NAME', HostName)
print("Sitename:", SiteName)

NOTIFY_URL = os.getenv('NOTIFY_URL', "http://172.16.0.65:5003/send-notification")

# Per-port alert mails are limited to a time window and to ports that are
# actually down. Previously every */2min run mailed every port including the
# healthy ones (status 2), around the clock. The checker itself still runs
# 24x7 so Redis/the LE BOD dashboard keeps updating -- only the mail is gated.
# Window is inclusive on both ends; pod clock is IST via the /etc/localtime
# hostPath mount, so datetime.now() (not utcnow()) is the correct clock.
# All knobs come from env (see bod-env-details ConfigMap); the values below
# are only the fallback when the env var is missing or malformed.
ALERT_WINDOW_START = os.getenv('ALERT_WINDOW_START', '09:00')
ALERT_WINDOW_END = os.getenv('ALERT_WINDOW_END', '23:30')
# Set ALERT_WINDOW_ENABLED=no to mail down ports 24x7 (still never up ports).
ALERT_WINDOW_ENABLED = os.getenv('ALERT_WINDOW_ENABLED', 'yes').strip().lower() in ('yes', 'true', '1')


def _parse_hhmm(value, default_minutes):
    try:
        hh, mm = value.strip().split(':')
        minutes = int(hh) * 60 + int(mm)
        if not (0 <= minutes <= 23 * 60 + 59):
            raise ValueError(value)
        return minutes
    except (AttributeError, ValueError):
        print(f"⚠️ Bad time '{value}', falling back to {default_minutes // 60:02d}:{default_minutes % 60:02d}")
        return default_minutes


def in_alert_window(now):
    if not ALERT_WINDOW_ENABLED:
        return True
    minutes = now.hour * 60 + now.minute
    return _parse_hhmm(ALERT_WINDOW_START, 540) <= minutes <= _parse_hhmm(ALERT_WINDOW_END, 1410)


def should_alert(status, now):
    """Mail a per-port alert only inside the window and only when it is down.

    status 2 == up (never mailed); anything else (0 down / 1 amber) mails.
    The --summary run does not go through here, so it is unaffected.
    """
    try:
        is_up = int(status) == 2
    except (TypeError, ValueError):
        is_up = False
    return (not is_up) and in_alert_window(now)


def load_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

def compile_host_results(host_info, timeout=5):
    results = []
    failed_hosts = []
    overall_success = True
    host = host_info['host']

    for port_info in host_info['ports']:
        port = port_info['port']
        remark = port_info.get('remark', 'No remark')
        result = {'host': host, 'port': port, 'remark': remark}

        try:
            with telnetlib.Telnet(host, port, timeout):
                result.update({
                    'isSuccess': True,
                    'status_msg': 'Connected',
                    'status': 2,
                    'err_msg': 'NA'
                })
                print(f"✅ Connected to {host}:{port}")
        except Exception as e:
            result.update({
                'isSuccess': False,
                'status_msg': 'Failed',
                'status': 0,
                'err_msg': str(e)
            })
            failed_hosts.append({
                'host': host,
                'port': port,
                'Description': remark,
                'error': str(e)
            })
            overall_success = False
            print(f"❌ Failed {host}:{port} - {e}")

        results.append(result)
        time.sleep(1)

    return results, overall_success, failed_hosts

def store_results_in_redis(env_name, results, execution_time, redis_key):
    final_data = {
        'executedOn': execution_time,
        'type': 'table',
        'status': 2 if all(item['isSuccess'] for item in results) else 0,
        'data': results
    }
    Redis().set(redis_key, str(final_data))
    print(f"✅ Stored Redis for {env_name}: {redis_key}")
    RMQ().le_refresh(Q='bod_update', mode="BOD")


def le_status_to_priority(status):
    """Map LE BOD status to notify priority for state-change mail.

    status 2 (green/up) → ok  (recovery mail only if previously down)
    status 1 (amber)    → warning
    status 0/3 (down)   → high
    """
    try:
        s = int(status)
    except (TypeError, ValueError):
        s = 0
    if s == 2:
        return "ok", False
    if s == 1:
        return "warning", True
    return "high", True

def send_to_notify(all_results):
    headers = {'Content-Type': 'application/json'}

    now = datetime.now()
    if not in_alert_window(now):
        print(f"⏸️ {now.strftime('%H:%M')} is outside the {ALERT_WINDOW_START}-{ALERT_WINDOW_END} alert window – no per-port mail sent.")
        return

    for item in all_results:
        if not should_alert(item.get('status', 0), now):
            print(f"🟢 Skipping mail for {item['host']}:{item['port']} – port is up.")
            continue
        priority, escalation_required = le_status_to_priority(item.get('status', 0))
        alert_data = {
            "event_id": f"telnet_{item['host']}_{item['port']}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "title": f"Telnet Status: {item['host']}:{item['port']}",
            "data": {
                "Server Name": SiteName,
                "host": item['host'],
                "port": item['port'],
                "description": item['remark'],
                "error": item['err_msg'],
                "status": int(item['status'])
            },
            "priority": priority,
            "escalation_required": escalation_required,
            "medium": "email",
            "source": "TelnetCheck"
        }
        print("📤 Sending alert:", alert_data)
        try:
            response = requests.post(NOTIFY_URL, json=alert_data, headers=headers)
            print("✅ Alert sent!" if response.status_code == 200 else f"❌ Failed: {response.text}")
        except Exception as e:
            print("❗ Exception sending alert:", e)

def send_summary_to_notify(all_results, success):
    status = 2 if success else 0
    headers = {'Content-Type': 'application/json'}

    summary = {
        "event_id": f"telnet_summary_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "title": f"Telnet Summary Report for {SiteName}",
        "data": {
            "hosts": all_results,
            "status": status,
            "isSummary": True
        },
        "priority": "medium",
        "escalation_required": False,
        "medium": "email",
        "source": "TelnetCheck",
        "status": status
    }

    print("📤 Sending summary...")
    try:
        response = requests.post(NOTIFY_URL, json=summary, headers=headers)
        print("✅ Summary sent!" if response.status_code == 200 else f"❌ Failed: {response.text}")
    except Exception as e:
        print("❗ Exception sending summary:", e)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Telnet Checker')
    parser.add_argument('--summary', action='store_true', help='Send a summary report instead of individual alerts')
    parser.add_argument('--config', default='/data/telnet.yaml', help='Path to YAML configuration file')
    args = parser.parse_args()

    config = load_config(args.config)
    if not config:
        print("❌ No configuration loaded.")
        exit(1)

    execution_time = datetime.now().strftime('%d-%b-%Y_%H%M%S.%f')
    all_results = []
    overall_success = True

    for env_name, env_config in config.items():
        print(f"🔍 Processing environment: {env_name}")
        if 'connections' not in env_config:
            print(f"⚠️ Skipping {env_name} – missing 'connections'")
            continue

        for host_info in env_config['connections']:
            host = host_info['host']
            # Redis_key is configured per-environment (env_config['Redis']['Redis_key']),
            # not per-connection -- host_info never has its own 'redis_key', so reading
            # it off host_info always missed and silently fell back to the
            # BOD_TelnetChecker-{env_name}-{host} default below, ignoring the
            # Redis_key set in telnet.yaml entirely.
            redis_key = f"{SiteName}:{env_config.get('Redis', {}).get('Redis_key', f'BOD_TelnetChecker-{env_name}-{host}')}"
            print(f"  ➡️ Checking host: {host} -> Redis key: {redis_key}")

            results, success, failed = compile_host_results(host_info)
            store_results_in_redis(f"{env_name}-{host}", results, execution_time, redis_key)

            all_results.extend(results)
            overall_success = overall_success and success

    if args.summary:
        send_summary_to_notify(all_results, overall_success)
    else:
        send_to_notify(all_results)
