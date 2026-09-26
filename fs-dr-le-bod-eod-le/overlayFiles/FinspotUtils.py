#!/bin/python3
# coding=utf-8
# -*- coding: utf-8 -*-
# -*- encoding: utf-8 -*-
from datetime import datetime
import subprocess
import requests
import logging
import socket
import redis
import yaml
import pika
import json
import os
import ast

from time import time
milliseconds = lambda: int(time() * 1000)

class RunCozy(object):
    def __init__(self):
        pass

    def parseYaml(self, filename):
        with open(filename) as file:
            return yaml.load(file, Loader=yaml.FullLoader)

    def executeCozyCommand(self, command):
        try:
            exception=False
            process = subprocess.Popen(command,shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            #process = subprocess.Popen(command.split(' '), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            rc = process.returncode
        except FileNotFoundError as ex:
            exception=True
            rc, out, err = (255,'',ex)
        if not exception:
            out, err = process.communicate()
        if err:
            out = err
        return {'rc' : rc , 'out' : out }

    def executeCozyCommandList(self, commandList):
        result = []
        for command in commandList:
            result.append(self.executeCozyCommand(command))
        return result

    def onlyRunexecuteCozyCommand(self,command):
        ret = self.executeCozyCommand(command)
        isSuccess = True
        return {'rc' : ret['rc'] , 'out' : ret['out'] , 'isSuccess': isSuccess}

    def executeCozyCommandAndReverseAnalyse(self,command,stringToSearch):
        ret = self.executeCozyCommand(command)
        isSuccess = True
        for i in str(ret['out']).strip().split('\n'):
            if str(stringToSearch) in str(i):
                out = i
                isSuccess = False
                break
        if isSuccess == False:
            out = ret['out']
        return {'rc' : ret['rc'] , 'out' : ret['out'] , 'isSuccess': isSuccess}


    def executeCozyCommandAndAnalyse(self,command,stringToSearch):
        ret = self.executeCozyCommand(command)
        isSuccess = False
        for i in str(ret['out']).strip().split('\n'):
            if str(stringToSearch) in str(i):
                out = i
                isSuccess = True
                break
        if isSuccess == False:
            out = ret['out']
        return {'rc' : ret['rc'] , 'out' : ret['out'] , 'isSuccess': isSuccess}

    def invokeRequest(self,url,method,headers,data):
        isSuccess = False
        status = 0
        headers = ast.literal_eval(headers)
        if data:
            data = ast.literal_eval(data)
        if method == 'POST':
            ret = self._post(url,headers,data)
        if method == 'GET':
            ret = self._get(url,headers)

        if "20" in str(ret.status_code):
            isSuccess = True
            status = 0
        return {'statusCode' : ret.status_code , 'out' : ret.text , 'isSuccess': isSuccess, 'status' : status}

    def _get(self,url,headers):
        try:
            result = requests.get(url, headers=headers, timeout=10)
            #print(" url = {}\n method = post\n headers = {}\n data = {}\n result = {} text = {}".format(url,headers,data,result,result.text))
        except Exception as ex:
            raise Exception(ex)
        return result

    def _post(self,url,headers,data):
        try:
            headers.update({'Content-type': 'application/json'})
            result = requests.post(url, data=json.dumps(data),headers=headers, timeout=10)
            #print(" url = {}\n method = post\n headers = {}\n data = {}\n result = {} text = {}".format(url,headers,data,result,result.text))
        except Exception as ex:
            raise Exception(ex)
        return result

    def validateLicense(self,command, warn=20, critical=15):
        ret = self.executeCozyCommand(command)
        isSuccess = False
        out = ret['out']
        #product = ""
        #for i in str(ret['out']).strip().split('\n'):
        #    if "Expiry" in i:
        #        #product = product + str(i) + "\n"
        #        out = i.split(' ')[-2]
        #    #if "-->" in i:
        #    #    product = product + str(i) + "\n"
        #_days_to_expire = (datetime.strptime(out, "%d/%m/%Y") - datetime.now()).days
        _days_to_expire = (datetime.strptime(out.rstrip(), "%d%m%Y") - datetime.now()).days
        if _days_to_expire < critical:
            isSuccess = False
            status = 0
        elif _days_to_expire < warn:
            isSuccess = False
            status = 1
        elif _days_to_expire > warn:
            isSuccess = True
            status = 2
        return {'rc' : ret['rc'] , 'Expiry Date' : out , 'isSuccess': isSuccess, 'status' : status}

    def k8sSSLLicense(self,command, warn=31, critical=15):
        ret = self.executeCozyCommand(command)
        isSuccess = False
        ssl_expiry = ret['out'].rstrip()
        print(ssl_expiry)
        _days_to_expire = (datetime.strptime(ssl_expiry.rstrip(), '%b %d, %Y %H:%M UTC') - datetime.now()).days
        if _days_to_expire < critical:
            isSuccess = False
            status = 0
        elif _days_to_expire < warn:
            isSuccess = False
            status = 1
        elif _days_to_expire > warn:
            isSuccess = True
            status = 2
        return {'rc' : ret['rc'] , 'Expiry Date' : ssl_expiry ,'Expiring in':_days_to_expire, 'isSuccess': isSuccess, 'status' : status}

class Redis(object):
    def __init__(self, host="", port="", db=0, password=""):
        self.db = db
        if host:
            self.host = host
        else:
            self.host = os.getenv('REDIS_HOST', "localhost")
        if port:
            self.port = port
        else:
            self.port = os.getenv('REDIS_PORT', 32268)
        if password:
            self.password = password
        else:
            self.password = os.getenv('REDIS_PASSWORD', 'REDIS_PASSWORD')
        self._connect()

    def _connect(self):
        self.channel = redis.StrictRedis(host=self.host, port=self.port, db=self.db, password=self.password)
        return self

    def set(self, key, value, status_key="", status_value=""):
        try:
            self.channel.set(key, value)
            if status_key:
                self.updateHealthStatus(status_key,status_value)
        except Exception as ex:
            raise Exception("REDIS SET : Ex = " + str(ex))

    def get(self, key):
        try:
            return self.channel.get(key).decode('ascii')
        except Exception as ex:
            raise Exception("REDIS GET : Ex = " + str(ex))

    def updateHealthStatus(self, key, new_value):
        try:
            value           = self.channel.get(key)
            existing_status = int(value.decode('ascii')) if value else 1
            update_value = existing_status * new_value
            if int(new_value) < int(existing_status):
                update_value = new_value
            print("Status Update [{}] : key : {} ValueExist : {} ValueNew : {} tobeUpdated : {}".format(milliseconds(), key, value, new_value, update_value))
            return self.set(key, update_value)
        except Exception as ex:
            raise Exception("REDIS updateHealthStatus : Ex = " + str(ex))

    def append(self, key, value, status_key="", status_value="", rc=False):
        try:
            exist_strvalue = self.channel.get(key)
            if exist_strvalue:
                exist_strvalue  = exist_strvalue.decode('ascii')
                exist_value     = ast.literal_eval(exist_strvalue)
                new_value       = ast.literal_eval(value)
                updated_value   = new_value.copy()
                updated_value['data'] = []
                updated_value['data'] = exist_value['data'] + new_value['data']
                updated_value['edit'] = exist_value.get('edit',[])
                updated_value['overallStatus'] = bool(exist_value.get('overallStatus',True) and rc)
                print("Redis append -> overallStatus ::: updated_value {} - exist_value {} - current_value {}".format(updated_value['overallStatus'],bool(exist_value.get('overallStatus',True)),rc))
                # -----------------------------------------------------------------
                isRed = 0
                isAmber = 0
                isGreen = 0
                isUnknown = 0
                for _status in updated_value['data']:
                    if _status['status'] == 0:
                        isRed +=1
                    elif _status['status'] == 1:
                        isAmber +=1
                    elif _status['status'] == 2:
                        isGreen +=1
                    else:
                        isUnknown +=1

                if isRed > 0:
                    updated_value['status'] = 0
                elif isAmber > 0:
                    updated_value['status'] = 1
                elif isGreen > 0:
                    updated_value['status'] = 2
                elif isUnknown > 0:
                    updated_value['status'] = 3
                # -----------------------------------------------------------------
                value = str(updated_value)
            else:
                first_value = ast.literal_eval(value)
                le_status = first_value['data'][0]['status']
                first_value['status'] = le_status
                value = str(first_value)
            self.set(key,value)
            if status_key:
                self.updateHealthStatus(status_key,status_value)
        except Exception as ex:
            raise Exception("REDIS APPEND : Ex = " + str(ex))

    def getBodEodkeys(self):
        try:
            _str_list = [i.decode('ascii') for i in self.channel.keys()]
            return [i for i in _str_list if "BOD" in i]
        except Exception as ex:
            raise Exception("REDIS GET-BOD-EOD : Ex = " + str(ex))

class RMQ(object):
    def __init__(self, host="", port="", virtual_host="/", user="linkedeye", password="linkedeye"):
        if host:
            self.host = host
        else:
            self.host = os.getenv('MQ_HOST', "localhost")
        if port:
            self.port = port
        else:
            self.port = os.getenv('MQ_PORT', 30916)
        self.virtual_host = virtual_host
        self.user = user
        self.password = password
        self._connect()

    def _connect(self):
        credentials = pika.PlainCredentials(self.user, self.password)
        self.connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=self.host, port=self.port,
                                      virtual_host=self.virtual_host, credentials=credentials))
        self.channel = self.connection.channel()
        return self

    def create_exchange(self, name, exchange_type="fanout", durable=False):
        try:
            self.channel.exchange_declare(exchange=name, exchange_type=exchange_type, durable=durable)
        except Exception as ex:
            raise Exception("MQ create_exchange : Ex = " + str(ex))

    def publish(self, exchange='delta_update', routing_key='', message=""):
        try:
            if message:
                self.channel.basic_publish(exchange=exchange, routing_key=routing_key, body=message)
        except Exception as ex:
            self._connect()
            self.channel.basic_publish(exchange=exchange, routing_key=routing_key, body=message)
            raise Exception("MQ publish : Ex = " + str(ex))

    def close(self):
        try:
            self.connection.close()
        except Exception as ex:
            raise Exception("MQ close : Ex = " + str(ex))

    def le_refresh(self, Q='bod_update', mode="ALL"):
        try:
            data = {}
            data["refresh"] = 1
            data["mode"] = mode.upper()
            data["site"] = socket.gethostname().split('.')[0]
            self.publish(exchange=Q, message=json.dumps(data))
            self.publish(exchange="map_update", message=json.dumps(data))
            self.close()
        except Exception as ex:
            raise Exception("MQ close : Ex = " + str(ex))


class FinLogger(object):
    def __init__(self, name, logname):
        self.FORMAT = '%(asctime)s,%(msecs)d - %(name)s [ %(levelname)s ] ::  %(message)s'
        self.logname = logname
        self.name = name


    def create(self):
        os.makedirs(os.path.dirname(self.logname), exist_ok=True)
        logging.basicConfig(filename=self.logname,
                            filemode='w',
                            format=self.FORMAT,
                            datefmt='%H:%M:%S',
                            level=logging.DEBUG)
        return logging.getLogger(self.name)

