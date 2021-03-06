import threading
import datetime
import struct
import time
import math
import random
from socket import *


def extract(data):
    # Format from https://github.com/limifly/ntpserver/
    unpacked = struct.unpack('!B B B b 11I', data[0:struct.calcsize('!B B B b 11I')])
    # Extract information
    info = {'leap': unpacked[0] >> 6 & 0x3, 'version': unpacked[0] >> 3 & 0x7, 'mode': unpacked[0] & 0x7,
            'stratum': unpacked[1], 'poll': unpacked[2], 'precision': unpacked[3],
            'root_delay': float(unpacked[4]) / 2 ** 16, 'root_dispersion': float(unpacked[5]) / 2 ** 16,
            'ref_id': unpacked[6], 'ref_timestamp': unpacked[7] + float(unpacked[8]) / 2 ** 32,
            'orig_timestamp': unpacked[9] + float(unpacked[10]) / 2 ** 32, 'orig_timestamp_high': unpacked[9],
            'orig_timestamp_low': unpacked[10], 'recv_timestamp': unpacked[11] + float(unpacked[12]) / 2 ** 32,
            'tx_timestamp': unpacked[13] + float(unpacked[14]) / 2 ** 32, 'tx_timestamp_high': unpacked[13],
            'tx_timestamp_low': unpacked[14]}
    # Return useful info for respose
    return info


def str2sec(mystr):
    secs_in = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800, 'M': 2629743, 'y': 31556926}
    if mystr[-1] in secs_in.keys():
        num = int(mystr[:-1])
        mag = secs_in[mystr[-1]]
    else:
        num = int(mystr)
        mag = 1
    return float(mag * num)


def packetize(info, param):
    # Format from https://github.com/limifly/ntpserver/
    # print param['ID'] + ' detected!'
    # Construct packet
    packed = struct.pack('!B B B b 11I',
                         (param['leap'] << 6 | param['version'] << 3 | param['mode']),
                         param['stratum'],
                         param['poll'],
                         param['precision'],
                         int(param['root_delay']) << 16 | int(
                             abs(param['root_delay'] - int(param['root_delay'])) * 2 ** 16),
                         int(param['root_dispersion']) << 16 |
                         int(abs(param['root_dispersion'] - int(param['root_dispersion'])) * 2 ** 16),
                         param['ref_id'],
                         int(param['ref_timestamp']),
                         int(abs(param['ref_timestamp'] - int(param['ref_timestamp'])) * 2 ** 32),
                         param['orig_timestamp_high'],
                         param['orig_timestamp_low'],
                         int(param['recv_timestamp']),
                         int(abs(param['recv_timestamp'] - int(param['recv_timestamp'])) * 2 ** 32),
                         int(param['tx_timestamp']),
                         int(abs(param['tx_timestamp'] - int(param['tx_timestamp'])) * 2 ** 32))
    # Return packet
    # int(abs(timestamp - int(timestamp)) * 2**32)
    return packed


class NTProxy(threading.Thread):
    # Stop Flag
    stopF = False
    # Force Step or date
    skim_step = float(0)
    skim_threshold = float(0)
    forced_step = float(0)
    forced_date = float(0)
    forced_random = False
    # Temporal control
    seen = {}

    # Constructor
    def __init__(self, sock):
        threading.Thread.__init__(self)
        if sock:
            self.step = 0
            self.ntp_delta = (datetime.date(*time.gmtime(0)[0:3]) - datetime.date(1900, 1, 1)).days * 24 * 3600
            self.stopF = False
            self.sock = sock
            self.sock.settimeout(5.0)

    # Force step or date
    def stop(self):
        self.stopF = True
        
    def set_skim_threshold(self, threshold):
        self.skim_threshold = str2sec(threshold)

    def set_skim_step(self, skim):
        self.skim_step = str2sec(skim) - self.skim_threshold

    def force_step(self, step):
        self.forced_step = str2sec(step)

    def force_date(self, date):
        if len(date) == len('2014-01-01 05:32'):
            pat = '%Y-%m-%d %H:%M'
        else:
            pat = '%Y-%m-%d %H:%M:%S'
        self.forced_date = float(datetime.datetime.strptime(date, pat).strftime('%s'))

    # Set the step to the future/past
    def select_step(self):
        # Get current date
        current_time = time.time()
        current_week_day = time.gmtime(current_time)[6]
        current_month_day = time.gmtime(current_time)[2]
        # Look for the same week and month day, minimum a thousand days in the future
        if self.forced_step == 0 and not self.forced_random:
            # Default Step
            week_day = 10000
            month_day = 10000
            future_time = current_time + (3 * 12 * 4 * 7 * 24 * 3600)
            while not ((week_day == current_week_day) and (month_day == current_month_day)):
                future_time = future_time + (7 * 24 * 3600)
                week_day = time.gmtime(future_time)[6]
                month_day = time.gmtime(future_time)[2]
        elif self.forced_random:
            min_time = math.floor(current_time)
            max_time = 4294967294 - self.ntp_delta  # max 32 bits - 1
            future_time = random.randint(min_time, max_time)
        else:
            # Forced Step
            future_time = current_time + self.forced_step
        self.step = future_time - current_time

    # Select a new time in the future/past
    def newtime(self, timestamp):
        current_time = time.time()
        skim_time = timestamp + self.skim_step - 5
        future_time = current_time + self.step
        if self.skim_step == 0:
            skim_time = 4294967294
        if self.forced_date == 0 and (skim_time > future_time):
            return future_time
        elif self.forced_date != 0 and (skim_time > self.forced_date):
            return self.forced_date
        else:
            return skim_time

    # Run Method
    def run(self):
        self.select_step()
        while not self.stopF:
            # When timeout we need to catch the exception
            try:
                data, source = self.sock.recvfrom(1024)
                info = extract(data)
                timestamp = self.newtime(info['tx_timestamp'] - self.ntp_delta)
                fingerprint, data = self.response(info, timestamp)
                if self.skim_step != 0:
                    for t in range(0, 10):
                        fingerprint, data = self.response(info, timestamp)
                socket.sendto(self.sock, data, source)
                epoch_now = time.time()
                if (not source[0] in self.seen) or (
                        (source[0] in self.seen) and (epoch_now - self.seen[source[0]]) > 2):
                    if self.forced_random:
                        self.select_step()
                    self.seen[source[0]] = epoch_now
                    # Year-Month-Day Hour:Mins
                    aux = time.gmtime(timestamp)
                    aux = time.gmtime(time.time())

            except:
                continue

    # Extract query information

    # Create response packet
    def response(self, info, timestamp):
        if info['leap'] == 0 and info['version'] == 4 and (info['mode'] == 3 or info['mode'] == 4):
            return self.response_osx(info, timestamp)
        if (info['leap'] == 3 or info['leap'] == 192) and info['version'] == 4 and info['mode'] == 3:
            return self.response_linux(info, timestamp)
        if info['version'] == 3:
            return self.response_win(info, timestamp)
        return self.response_default(info, timestamp)

    def generate_param(self, info, timestamp):
        # Format from https://github.com/limifly/ntpserver/
        # Define response params
        ntp_timestamp = timestamp + self.ntp_delta
        param = {'ID': 'Unknown', 'leap': 0, 'version': info['version'], 'mode': 4, 'stratum': 3, 'poll': 17,
                 'precision': 0, 'root_delay': 0.01, 'root_dispersion': 0.01, 'ref_id': 423814661,
                 'ref_timestamp': ntp_timestamp - 5, 'orig_timestamp': 0,
                 'orig_timestamp_high': info['tx_timestamp_high'], 'orig_timestamp_low': info['tx_timestamp_low'],
                 'recv_timestamp': ntp_timestamp, 'tx_timestamp': ntp_timestamp, 'tx_timestamp_high': 0,
                 'tx_timestamp_low': 0}
        return param

    def response_linux(self, info, timestamp):
        param = self.generate_param(info, timestamp)
        param['ID'] = 'Linux'
        # param['leap'] = 4
        # param['version'] = info['version']
        # param['mode'] = 4
        # Construct packet
        return param['ID'], packetize(info, param)

    def response_osx(self, info, timestamp):
        param = self.generate_param(info, timestamp)
        param['ID'] = 'Mac OS X'
        # param['ref_id'] = 0 # 17.72.133.55
        # param['leap'] = 0
        # param['version'] = 4
        # param['mode'] = 4
        # param['poll'] = 9
        # Construct packet
        return param['ID'], packetize(info, param)

    def response_win(self, info, timestamp):
        param = self.generate_param(info, timestamp)
        param['ID'] = 'Windows'
        # param['version'] = 3
        # Construct packet
        return param['ID'], packetize(info, param)

    def response_default(self, info, timestamp):
        param = self.generate_param(info, timestamp)
        # Construct packet
        return param['ID'], packetize(info, param)
