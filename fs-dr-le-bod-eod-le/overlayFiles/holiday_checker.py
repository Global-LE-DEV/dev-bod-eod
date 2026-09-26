#!/bin/python3
## coding=utf-8
## -*- coding: utf-8 -*-
## -*- encoding: utf-8 -*-
from FinspotUtils import RunCozy, FinLogger, Redis, RMQ
import pandas as pd
from datetime import datetime
import numpy as np
import socket
import json
import os

columns_to_be_removed = ['date']
today = datetime.now().strftime("%d-%b-%y")
YEAR = datetime.now().strftime("%Y")
now = str(datetime.strftime(datetime.now() , '%d-%m-%Y %H:%M:%S'))
#HostName = socket.gethostname().split('.')[0]

print(" HOLIDAY CHECKER EXECUTING ")
def calendarToEnvVariables(calendar, flag="Holiday"):
    REDIS_KEY = str(flag)
    print("REDIS_KEY",REDIS_KEY)
    HolidaySegments = []
    NonHolidaySegments=[]
    a=os.getenv('CALENDAR_REDIS_PUSH')
    print("hi--------------------------")
    calendar = pd.read_csv(calendar, index_col=0).fillna('-')
    print("bye-----------------------")
    REDIS_PUSH = int(os.getenv('CALENDAR_REDIS_PUSH', 0))

    print("REDIS_PUSH",REDIS_PUSH)
    if REDIS_PUSH == 1:
        REDIS_VALUE=str({'executedOn' : now, 'type': 'matrix', "data" : json.loads(calendar.to_json(orient = "index")) })
        Redis().set(REDIS_KEY,REDIS_VALUE)
        print(" GOING TO PUSH IN REDIS ")

calendarToEnvVariables("/data/holiday_calendar_"+str(YEAR)+".csv",flag="Holiday")
calendarToEnvVariables("/data/mock_calendar_"+str(YEAR)+".csv",flag="Mock")
print(" REDIS PUSH DONE ")
