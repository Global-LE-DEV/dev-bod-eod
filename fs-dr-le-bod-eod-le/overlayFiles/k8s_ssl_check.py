from FinspotUtils import RunCozy, FinLogger, Redis, RMQ
from datetime import datetime
import socket
import argparse
import json
import os
import requests

NOTIFY_API = os.getenv('NOTIFY_URL',"http://172.16.0.65:5003/send-notification")

class VirtualEngineer(RunCozy, FinLogger):
    def __init__(self, HOLIDAY_CHECK, MOCK_CHECK):
        self.HOLIDAY_CHECK = int(HOLIDAY_CHECK)
        self.MOCK_CHECK = int(MOCK_CHECK)
        self.isPassive = os.getenv('PASSIVE_MODE', 'Yes')
        self.HostName = socket.gethostname().split('.')[0]
        self.SiteName = os.getenv('SITE_NAME', self.HostName)
        self.K8S_SSL_REDIS_KEY = f"{self.SiteName}:BOD_K8S_SSL_Expiry"
        self.K8S_SSL_REDIS_VALUE = ""
        self.now = datetime.now().strftime('%d-%m-%Y %H:%M:%S')

    def k8sCheck(self):
        ret = []
        command = (
            "kubeadm certs check-expiration 2>/dev/null | grep ^'apiserver ' "
            "| awk -F'apiserver|UTC' '{print $2 \" UTC\"}' | xargs"
        )
        status = self.k8sSSLLicense(command,warn=30,critical=15)
        del status['rc']
        ret.append(status)
        return ret


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

def send_to_notify(hostname, result, exec_time, le_status=2):
    headers = {'Content-Type': 'application/json'}
    for item in result:
        # Stable title so DOWN→UP recovery uses the same notify key.
        item_status = item.get('status', le_status)
        priority, escalation_required = le_status_to_priority(item_status)
        alert_data = {
            "event_id": f"k8s_ssl_{hostname}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "title": f"Kubernetes SSL Check: {hostname}",
            "data": {
                "host": hostname,
                "description": item.get("msg", "K8s SSL cert check"),
                "Expiry date": item.get('Expiry Date', 'NA'),
                "Expiring in(Days)": item.get('Expiring in', 'NA'),
                "status": item_status,
                "error": item.get("err_msg", "NA")
            },
            "priority": priority,
            "escalation_required": escalation_required,
            "medium": "email",
            "source": "K8sSSLCheck"
        }

        print("Sending notify:", alert_data)
        try:
            response = requests.post(NOTIFY_API, json=alert_data, headers=headers)
            print("Notification sent!" if response.status_code == 200 else f"Failed: {response.text}")
        except Exception as e:
            print("❗ Exception sending notification:", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--holiday_check", default=0, required=False, help="Holiday Checker. Default:0")
    parser.add_argument("--mock_check", default=0, required=False, help="Mock Checker. Default:0")
    args = parser.parse_args()

    obj = VirtualEngineer(HOLIDAY_CHECK=args.holiday_check, MOCK_CHECK=args.mock_check)
    status = obj.k8sCheck()

    print(obj.K8S_SSL_REDIS_KEY)
    bod_value = 1 if status[-1]['isSuccess'] else 0
    le_status = status[-1]['status']
    overallStatus = bool(bod_value)

    obj.K8S_SSL_REDIS_VALUE = str({
        'overallStatus': overallStatus,
        'executedOn': obj.now,
        'type': 'table',
        'status': le_status,
        'data': status
    })

    print(obj.K8S_SSL_REDIS_VALUE)
    Redis().set(obj.K8S_SSL_REDIS_KEY, obj.K8S_SSL_REDIS_VALUE, status_key='bod', status_value=bod_value)
    RMQ().le_refresh(Q='bod_update', mode="BOD")

    # notify call
    send_to_notify(obj.HostName, status, obj.now, le_status)
