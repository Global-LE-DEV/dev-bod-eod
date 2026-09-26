import requests
import json
import websocket
import argparse
import yaml
from FinspotUtils import Redis, RMQ, RunCozy, FinLogger
from datetime import datetime
from pytz import timezone
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import ssl, socket
import traceback
import tabulate
import threading
import os

NOTIFY_URL = os.getenv('NOTIFY_URL', "http://172.16.0.65:5003/send-notification")

# Global settings
DEFAULT_TIMEOUT = 5  # seconds

# Load settings from YAML file
with open('/data/url.yaml') as f:
    settings = yaml.safe_load(f)

# Initialize eesult dictionary
res = {}
res['executedOn'] = datetime.now().astimezone(timezone('Asia/Kolkata')).strftime('%d-%b-%Y_%H%M%S.%f')
res['type'] = "table"
res['data'] = []

class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, timeout=DEFAULT_TIMEOUT, *args, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        timeout = kwargs.get("timeout", self.timeout)
        kwargs["timeout"] = timeout
        return super().send(request, **kwargs)

class CheckURL:
    def __init__(self, uri, ssl_warn, ssl_critical, timeout=DEFAULT_TIMEOUT):
        self.status = {
            'url': uri,
            'isSuccess': False,
            'status': 0,
            'status_code': 0,
            'err_msg': "NA",
            'SSL_Expiry_Date': "NA",
            'SSL_Days_Left': "NA"
        }
        self.SSL_WARN = ssl_warn
        self.SSL_CRITICAL = ssl_critical
        self.timeout = timeout

    def getSSLExpiry(self):
        if 'https' in self.status['url'] or 'wss' in self.status['url']:
            SSL_URL = self.status['url'].split('://')[-1].split('/')[0]
            try:
                with socket.create_connection((SSL_URL, 443), timeout=self.timeout) as sock:
                    with ssl.create_default_context().wrap_socket(sock, server_hostname=SSL_URL) as ssock:
                        certificate = ssock.getpeercert()

                self.status['SSL_Expiry_Date'] = certificate['notAfter']
                self.status['SSL_Days_Left'] = (datetime.strptime(certificate['notAfter'], "%b %d %H:%M:%S %Y %Z") - datetime.now()).days
                if self.status['SSL_Days_Left'] < self.SSL_CRITICAL:
                    self.status['isSuccess'] = False
                    self.status['status'] = 0
                elif self.status['SSL_Days_Left'] < self.SSL_WARN:
                    self.status['isSuccess'] = False
                    self.status['status'] = 1
                else:
                    self.status['isSuccess'] = True
                    self.status['status'] = 2
            except Exception as e:
                self.status['err_msg'] = str(e).replace('"', "'")

    def checkHTTP(self):
        try:
            http = requests.Session()
            retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
            adapter = TimeoutHTTPAdapter(timeout=self.timeout)
            http.mount("http://", adapter)
            http.mount("https://", adapter)
            r = http.get(self.status['url'], timeout=self.timeout)
            self.status['status_code'] = r.status_code
            self.status['isSuccess'] = (self.status['status_code'] == 200)
            self.status['status'] = 2 if self.status['status_code'] == 200 else 0
            if '40' in str(self.status['status_code']):
                self.status['status'] = 1
        except Exception as ex:
            self.status['err_msg'] = str(ex).replace('"', "'")
        return self.status['isSuccess']

    def checkWSS(self):
        try:
            ws = websocket.WebSocket(timeout=self.timeout, close_timeout=self.timeout)
            ws.connect("wss://" + self.status['url'].split('://')[-1])
            self.status['status_code'] = ws.status
            self.status['isSuccess'] = (self.status['status_code'] == 101)
            self.status['status'] = 2 if self.status['status_code'] == 101 else 0
            ws.close()
        except Exception as ex:
            self.status['err_msg'] = str(ex).replace('"', "'")
        return self.status['isSuccess']

    def set_timeout(self):
        self.status['err_msg'] = "Request timed out"
        self.status['status'] = 3
        self.status['isSuccess'] = False

    def run(self):
        timer = threading.Timer(self.timeout, self.set_timeout)
        timer.start()
        try:
            if 'http' in self.status['url']:
                self.checkHTTP()
            if 'wss' in self.status['url']:
                self.checkWSS()
            if self.status['isSuccess']:
                self.getSSLExpiry()
        finally:
            timer.cancel()
        return self.status

class LE(RunCozy, FinLogger):
    def __init__(self):
        self.HostName = socket.gethostname().split('.')[0]
        self.SiteName = os.getenv('SITE_NAME', self.HostName)
        self.REDIS_KEY = f"{self.SiteName}:BOD_URLChecker"

    def cacheThis(self, key="", value="", status_key="bod", status_value=0):
        try:
            if not key:
                key = self.REDIS_KEY
            if not value:
                value = self.REDIS_VALUE
            print("VALUE", value)
            Redis().set(key, value, status_key, status_value)
            print("===Pushed====> {}".format(key))
            RMQ().le_refresh(Q='bod_update', mode="BOD")
            print("===Refresh Signal sent====> ")
        except Exception as ex:
            print("===== REDIS EXCEPTION : \n" + str(ex) + "\n=====\n")
            traceback.print_tb(ex.__traceback__)

# Initialize the overall status variables


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

def send_to_notify(results):
    #url = "http://172.16.0.65:5003/send-notification"
    headers = {'Content-Type': 'application/json'}
    for item in results:
        priority, escalation_required = le_status_to_priority(item.get('status', 0))
        alert_data = {
            "event_id": f"urlcheck_{item['url'].replace('://', '_').replace('/', '_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "title": f"URL Check Result: {item['url']}",
            "data": {
                "host": item['url'],
                "description": f"Status Code: {item.get('status_code', 'NA')}, SSL: {item.get('SSL_Expiry_Date', 'NA')}",
                "SSL_days_left":item.get('SSL_Days_Left','NA'),
                "error": item.get('err_msg', 'NA'),
                "status": item.get('status', 0)
            },
            "priority": priority,
            "escalation_required": escalation_required,
            "medium": "email",
            "source": "URLChecker"
        }
        print("Sending alert:", alert_data)
        try:
            response = requests.post(NOTIFY_URL, json=alert_data, headers=headers)
            print("✅ Alert sent!" if response.status_code == 200 else f"❌ Failed: {response.text}")
        except Exception as e:
            print("❗ Error sending alert:", e)

def send_summary_to_notify(results,status):
    #url = "http://172.16.0.65:5003/send-notification"
    headers = {'Content-Type': 'application/json'}
    summary_data = {
        "event_id": f"urlcheck_summary_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "title": "URL Check Summary Report",
        "data": {
            "hosts": results,
            "status":status,
            "isSummary": True
        },

        "status":2,
        "priority": "medium",
        "escalation_required": False,
        "medium": "email",
        "source": "URLChecker"
    }
    print("Sending summary alert...")
    try:
        response = requests.post(NOTIFY_URL, json=summary_data, headers=headers)
        print("✅ Summary sent!" if response.status_code == 200 else f"❌ Failed: {response.text}")
    except Exception as e:
        print("❗ Error sending summary:", e)



HostName = socket.gethostname().split('.')[0]
SiteName = os.getenv('SITE_NAME', HostName)
print("Sitename",SiteName)

# Add this after imports and before YAML load
parser = argparse.ArgumentParser()
parser.add_argument('--summary', action='store_true', help='Send summary report instead of individual alerts')
args = parser.parse_args()


# Loop through all environments in the YAML file
for env_name, env_config in settings.items():
    overallStatus = True
    isRed = 0
    isAmber = 0
    isGreen = 0
    isUnknown = 0
    res['data']=[]
    print(f"Processing environment: {env_name}")

    # Get URLs and Redis configurations for the current environment
    urls_to_check = env_config['urls']
    redis_host = os.getenv('REDIS_HOST',"172.16.0.65")
    redis_port = os.getenv('REDIS_PORT',32268)
    redis_password = os.getenv('REDIS_PASSWORD','REDIS_PASSWORD')
    redis_key = f"{SiteName}:{env_config['REDIS']['KEY']}"

    # Perform URL checks for each URL in the list
    for url in urls_to_check:
        status = CheckURL(url, 45, 10, timeout=DEFAULT_TIMEOUT).run()
        overallStatus = overallStatus and bool(status['isSuccess'])
        if status['status'] == 0:
            isRed += 1
        elif status['status'] == 1:
            isAmber += 1
        elif status['status'] == 2:
            isGreen += 1
        elif status['status'] == 3:
            isUnknown += 1
        res['data'].append(status)
        print("URL {} {}:{}".format(status['url'], status['status_code'], status['status']))

    # Overall status and final results for the current environment
    res['overallStatus'] = overallStatus
    if isRed > 0:
        le_status = 0
    elif isAmber > 0:
        le_status = 1
    elif isGreen > 0:
        le_status = 2
    elif isUnknown > 0:
        le_status = 3
    res['status'] = le_status

    # Push data to Redis for the current environment
    print(f"Updating Redis for environment: {env_name}")
    print("RES FORMAT", str(res))
    Redis().set(redis_key, str(res))
    RMQ().le_refresh(Q='bod_update', mode="BOD")

    # Print table format for URL statuses
    table_data = []
    for item in res['data']:
        ssl_days_left = str(item.get('SSL_Days_Left', 'NA'))
        status_color = "\033[91m" if item['status'] == 0 else ("\033[93m" if item['status'] == 1 else "\033[92m")
        row_color = "\033[91m" if item['status'] == 0 else ("\033[93m" if item['status'] == 1 else "\033[92m")
        reset_color = "\033[0m"
        table_data.append([
            row_color + item['url'] + reset_color,
            row_color + str(item['status_code']) + reset_color,
            row_color + str(item['SSL_Expiry_Date']) + reset_color,
            row_color + ssl_days_left + reset_color
        ])

    headers = ['URL', 'Status Code', 'SSL Expiry', 'Days Left']
    print(tabulate.tabulate(table_data, headers=headers, tablefmt="fancy_grid"))
    if args.summary:
        send_summary_to_notify(res['data'],overallStatus)
    else:
        send_to_notify(res['data'])


