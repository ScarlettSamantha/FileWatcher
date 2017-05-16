# !/usr/bin/env python

from os import path
from sys import exit, argv

from filewatcher import FileWatcherDaemon

__author__ = "Davi Verheul"
__copyright__ = "SupportDesk B.V 2017"
__credits__ = ["Davi Verheul"]

__version__ = "1.0.0"
__maintainer__ = "Davi Verheul"
__email__ = "davi.verheul@supportdesk.nu"
__status__ = "production"


def print_usage(current=None):
    print("usage: %s start|stop|restart" % current if current else '')


if __name__ == "__main__":
    daemon = FileWatcherDaemon(path.abspath('./file_watcher.pid'))
    if len(argv) == 2:
        if 'start' == argv[1]:
            daemon.start()
        elif 'stop' == argv[1]:
            daemon.stop()
        elif 'restart' == argv[1]:
            daemon.restart()
        else:
            print_usage(argv[1])
            exit(2)
        exit(0)
    else:
        print_usage(argv[0])
        exit(2)
