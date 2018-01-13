#!/usr/bin/env python
from __future__ import print_function
from urllib2 import URLError
from time import sleep
from requests import ConnectionError
from subprocess import call
import signal
import requests
import socket
import netifaces as ni
import shifter
import os
import psutil

PORT = 9091
VPN_INTERFACE = 'tun0'
LOCAL_INTERFACE = 'eth0'
WAIT_CYCLE = 30
TM_LOG = "/var/log/transmission-daemon/transmission-daemon.log"

def sigterm_handler(_signo, _stack_frame):
    print("sigterm_handler executed, %s, %s" % (_signo, _stack_frame))
    sys.exit(0)

def tail_log():
    """Tail transmission log in background."""
    call("pkill -f tail", shell=True)
    call("tail -F {} &".format(TM_LOG), shell=True)

def torrents_active():
    """Return boolean if any torrents are active"""
    ip = ni.ifaddresses(LOCAL_INTERFACE)[ni.AF_INET][0]['addr']
    port = PORT
    client = shifter.Client(host=ip, port=port)
    try:
        active_torrent_count = client.session.stats()[u'active_torrent_count']
        print("Active torrents: {}".format(active_torrent_count))
        if active_torrent_count:
            return True
    except URLError as e:
        print("Can't connect to transmission on {}:{}".format(ip, port))
    return False

def get_vpn_ip():
    """Get VPN ip, return None if we don't have it yet"""
    try:
        if VPN_INTERFACE in ni.interfaces():
            vpn_ip = ni.ifaddresses(VPN_INTERFACE)[ni.AF_INET][0]['addr']
            socket.inet_aton(vpn_ip)  # Validate ip
            return vpn_ip
    except socket.error, KeyError:
        pass
    return None


def get_bind_to_ip():
    """Get the ip transmission should bind to
      If there are no torrents active or we're
      not connected to the vpn, bind to localhost."""
    localhost = '127.0.0.1'
    if not torrents_active():
        return localhost
    vpn_ip = get_vpn_ip()
    if vpn_ip:
        return vpn_ip
    return localhost


class CmdLine(object):
    """Command line object stores arg in dict
       Allows single args to be changed by updating keys."""
    def __init__(self, start_cmd):
        self.start_cmd = start_cmd
        self.args = {}

    def __repr__(self):
        full_cmd = self.start_cmd
        for (key, value) in sorted(self.args.iteritems()):
            full_cmd += " --{}".format(key)
            if value is not None:
                full_cmd += " {}".format(value)
        full_cmd = full_cmd.rstrip()
        return full_cmd

class TransmissionDaemon(object):
    """Make it easy to restart daemon on bind change."""
    def __init__(self):
        local_ip = ni.ifaddresses(LOCAL_INTERFACE)[ni.AF_INET][0]['addr']
        self.cmdline = CmdLine(start_cmd='/usr/bin/transmission-daemon')
        self.base_args = {
                          'rpc-bind-address' : local_ip,
                          'allowed'          : '127.0.0.1,192.168.*.*,172.18.*.*',
                          'download-dir'     : '/mnt/Downloads_Completed',
                          'incomplete-dir'   : '/mnt/Downloads_In_Progress',
                          'logfile'          : TM_LOG,
                          'global-seedratio' : 1.0,
                          'no-utp'           : None,
                          'portmap'          : None,
                        }
        self.daemon = DaemonRestarter('transmission-daemon')

    def set_bind(self, ip):
        self.cmdline.args = dict(self.base_args)
        self.cmdline.args['bind-address-ipv4'] = ip
        if ip == '127.0.0.1':
            # dht, lpd, utp can contribute to bandwidth
            # usage when there are no active torrents
            self.cmdline.args['no-dht'] = None
            self.cmdline.args['no-lpd'] = None
        else:
            self.cmdline.args['dht'] = None
            self.cmdline.args['lpd'] = None
        self.daemon.cmdline = str(self.cmdline)


class DaemonRestarter(object):
    """Restart daemon if command line changes"""
    def __init__(self, process_name):
        self.process_name = process_name
        self._cmdline = None

    def restart(self):
        tm_procs = [proc for proc in psutil.process_iter(attrs=['name']) if proc.name() == self.process_name]
        for p in tm_procs:
            print("Terminating {}".format(p))
            p.terminate()
        _, alive = psutil.wait_procs(tm_procs, timeout=3)
        for p in alive:
            print("Killing {}".format(p))
            p.kill()
        print("Starting {}".format(self._cmdline))
        call(self._cmdline, shell=True)

    @property
    def cmdline(self):
        return self._cmdline

    @cmdline.setter
    def cmdline(self, value):
        if self._cmdline != value:
            print("Command line changed from {} to {}".format(self._cmdline, value))
            self._cmdline = value
            self.restart()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, sigterm_handler)
    tail_log()
    tm_daemon = TransmissionDaemon()
    while True:
        tm_daemon.set_bind(get_bind_to_ip())
        sleep(WAIT_CYCLE)
