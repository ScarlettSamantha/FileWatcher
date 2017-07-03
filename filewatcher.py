#!/usr/bin/env python3.5

"""filewatcher.py Contains various classes to allow a systems file system to be monitored and logged"""

import atexit
import json
import logging
import os
import signal
import smtplib
import sys
import time
from email.message import EmailMessage

import dict_digger
from watchdog.events import FileSystemEventHandler, EVENT_TYPE_MOVED
from watchdog.observers import Observer

__author__ = "Scarlett Verheul"
__copyright__ = "SupportDesk B.V 2017"
__credits__ = ["Scarlett Verheul"]

__version__ = "1.0.0"
__maintainer__ = "Scarlett Verheul"
__email__ = "scarlett@supportdesk.nu"
__status__ = "production"


class Daemon:
    """A generic daemon class.

    Usage: subclass the daemon class and override the run() method."""

    def __init__(self, pid_file):
        self.pid_file = pid_file

    def daemonize(self):
        """Demonize class. UNIX double fork mechanism."""

        try:
            pid = os.fork()
            if pid > 0:
                # exit first parent
                sys.exit(0)
        except OSError as err:
            sys.stderr.write('fork #1 failed: {0}\n'.format(err))
            sys.exit(1)

        # decouple from parent environment
        os.chdir(os.getcwd())
        os.setsid()
        os.umask(0)

        # do second fork
        try:
            pid = os.fork()
            if pid > 0:
                # exit from second parent
                sys.exit(0)
        except OSError as err:
            sys.stderr.write('fork #2 failed: {0}\n'.format(err))
            sys.exit(1)

        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        si = open(os.devnull, 'r')
        so = open(os.devnull, 'a+')
        se = open(os.devnull, 'a+')

        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())

        # write pid file
        atexit.register(self.delete_pid)

        pid = str(os.getpid())
        with open(self.pid_file, 'w+') as f:
            f.write(pid + '\n')

    def delete_pid(self):
        os.remove(self.pid_file)

    def start(self):
        """Start the daemon."""

        # Check for a pid file to see if the daemon already runs
        try:
            with open(self.pid_file, 'r') as pf:
                pid = int(pf.read().strip())
        except IOError:
            pid = None

        if pid:
            message = "pid file {0} already exist. Daemon already running?\n"
            sys.stderr.write(message.format(self.pid_file))
            sys.exit(1)

        # Start the daemon
        self.daemonize()
        self.run()

    def stop(self):
        """Stop the daemon."""

        # Get the pid from the pid file
        try:
            with open(self.pid_file, 'r') as pf:
                pid = int(pf.read().strip())
        except IOError:
            pid = None

        if not pid:
            message = "pid file {0} does not exist. Daemon not running?\n"
            sys.stderr.write(message.format(self.pid_file))
            return  # not an error in a restart

        # Try killing the daemon process
        try:
            while 1:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.1)
        except OSError as err:
            e = str(err.args)
            if e.find("No such process") > 0:
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)
            else:
                print(str(err.args))
                sys.exit(1)

    def restart(self):
        """Restart the daemon."""
        self.stop()
        self.start()

    def run(self):
        pass
        """You should override this method when you subclass Daemon.

        It will be called after the process has been daemonized by 
        start() or restart()."""


class Config(object):
    """
    A simple json config handler.
    """

    def __init__(self, config_path=None, data=None):
        """
        WIll load the config path and ether sets the data or loads the file
        :param config_path: 
        :param data: 
        """
        if config_path is not None:
            self.loads(config_path)
        elif data is not None:
            self.data = data

    def loads(self, config_path):
        """
        Will load the file in to the data property
        :param config_path: 
        :return: 
        """
        self.data = json.load(open(config_path, 'r'))

    def set(self, key, value):
        """
        Will set a value to a given key path.
        :param key: 
        :param value: 
        :return: 
        """
        if isinstance(key, (list, tuple)) and key.__len__ > 1:
            result = dict_digger.dig(self, key[:-1])
            result[key[-1]] = value
        else:
            self.data[key] = value

    def get(self, key):
        """
        Will fetch a config value.
        :param key: 
        :return: 
        """
        if isinstance(key, str):
            key = (key,)
        result = dict_digger.dig(self.data, *key)
        if not result:
            if isinstance(key, (tuple, dict, list)):
                raise KeyError("Could not find requested key '%s' in configuration" % '.'.join(key))
            else:
                raise KeyError("Could not find requested key '%s' in configuration" % key)
        return result

    def has(self, key):
        """
        Does a get call and will catch the exception.
        :param key: 
        :return: 
        """
        try:
            self.get(key)
        except KeyError:
            return False
        return True


class EventHandler(FileSystemEventHandler):
    """
    The main event handler that will be used when a file system change is detected.
    """

    MAIL_LOG_MODE_LOCALHOST = 'localhost'
    MAIL_LOG_MODE_REMOTE = 'smtp'

    DIRECTORY_IDENTIFIER = 'directory'
    FILE_IDENTIFIER = 'file'

    EOL = '\r\n'
    TAB = '\t'
    TAB_TAB = '\t\t'

    def __init__(self, config):
        super(FileSystemEventHandler, self).__init__()
        if not isinstance(config, Config):
            raise ValueError("config parameter has to be instance of the config class.")
        self.config = config
        self.event_cache = {}
        self.last_detection = None
        self.cache_max_size = self.config.get('cache_max_size')
        self.cache_timeout = self.config.get('cache_timeout')
        self.logger = None

    def on_any_event(self, event):
        """
        This function will be called when a event is recieved.
        :param event: 
        :return: 
        """
        self.event_cache[str(self.time_to_ms(time.time()))] = event
        self.last_detection = self.time_to_ms(time.time())

    def cache_tick(self):
        """
        This is controlled by the config poll_rate property and flushes the cache when needed.
        :return: 
        """
        if self.cache_should_burst():
            self.flush()

    def cache_should_burst(self):
        """
        Determines if the cache should be flushed to email/log.
        :return: 
        """
        if self.event_cache.__len__() > 0 and self.time_to_ms(time.time()) > (
                    self.last_detection + self.time_to_ms(self.cache_timeout)):
            return True
        elif self.event_cache.__len__() > self.cache_max_size != 0:
            return True
        return False

    def flush(self):
        """
        Will flush the cache to mail and/or log and empty the cache.
        :return: 
        """
        if self.config.get(('file_log', 'enabled')):
            self.log_to_file()
        if self.config.get(('email_log', 'enabled')):
            self.log_to_mail()
        self.event_cache.clear()

    def log_to_file(self):
        """
        Logs the events cache to a log file
        :return: 
        """
        logging.basicConfig(filename=self.config.get(('file_log', 'file_name')),
                            format=self.config.get(('file_log', 'basic_format')))
        logger = logging.getLogger(self.config.get(('file_log', 'logger_name')))
        for event in self.events_to_log_rule(self.event_cache):
            logger.warning(event)

    def log_to_mail(self):
        """
        Logs the events cache to a email via local mail or smtp.
        :return: 
        """
        email = EmailMessage()
        message_body = (self.get_email_template() % {**self.events_to_template(self.event_cache),
                                                     'project_name': self.config.get('project_name')})
        email.set_content(message_body)
        if self.config.get('debug'):
            print(message_body)
        else:
            email['Subject'] = self.config.get(('email_log', 'subject'))
            email['From'] = self.config.get(('email_log', 'from'))
            email['To'] = ','.join(self.config.get(('email_log', 'to')))
            if self.config.get(('email_log', 'mode')) == self.MAIL_LOG_MODE_LOCALHOST:
                server = smtplib.SMTP('localhost')
            elif self.config.get(('email_log', 'mode')) == self.MAIL_LOG_MODE_REMOTE:
                server = smtplib.SMTP(self.config.get(('email_log', 'smtp', 'host')),
                                      port=self.config.get(('email_log', 'smtp', 'port')))
                server.login(self.config.get(('email_log', 'smtp', 'user')),
                             self.config.get(('email_log', 'smtp', 'password')))
            else:
                raise KeyError("Invalid smtp mode can ether be %s or %s" % (
                    self.MAIL_LOG_MODE_LOCALHOST, self.MAIL_LOG_MODE_REMOTE))
            server.send_message(email)
            server.quit()

    def events_to_template(self, events):
        """
        Goes through the events and formats them to a nice string for template use
        :param events: 
        :return: 
        """
        data = {'modified': '', 'created': '', 'moved': '', 'deleted': ''}
        for micro_time, event in events.items():
            event_pattern = self.config.get(('email_log', '%s_pattern' % event.event_type))
            data[event.event_type] += (
                event_pattern % {**self.event_to_pattern(event, micro_time),
                                 **{'eol': self.EOL, 'indent': self.TAB_TAB}})
        if data.__len__() != 0:
            events = ','.join({k: v for k, v in data.items() if v})
            data['events'] = events
        return data

    def events_to_log_rule(self, events):
        """
        Will format a event to a log rule format.
        :param events: 
        :return: 
        """
        data = []
        for micro_time, event in events.items():
            event_pattern = self.config.get(('email_log', '%s_pattern' % event.event_type))
            data.append((event_pattern % {**self.event_to_pattern(event, micro_time), **{'eol': '', 'indent': ''}}))
        return sorted(data)

    def event_to_pattern(self, event, time_value: str):
        """
        Will prepare a event for conversion to a pattern.
        :param event: 
        :param time_value: 
        :return: 
        """
        return {
            'time': self.ms_to_time(int(time_value), self.config.get('time_format')),
            'path': os.path.abspath(event.src_path),
            'to': event.dest_path if event.event_type == EVENT_TYPE_MOVED else None,
            'type': self.DIRECTORY_IDENTIFIER if event.is_directory else self.FILE_IDENTIFIER
        }

    @staticmethod
    def time_to_ms(time_value: int):
        """
        Will convert a current timestamp to one with microseconds
        :param time_value: int
        :return: int
        """
        return int(round(time_value * 1000.0, 0))

    @staticmethod
    def ms_to_time(time_value: int, pattern: str):
        """
        Will convert microsecond timestamp back to normal timestamp
        :param time_value: int
        :param pattern: str
        :return: str
        """
        return str(time.strftime(pattern, time.localtime(round((time_value / 1000.0), 0))))

    def get_email_template(self):
        """
        Will read the email template file.
        :return: str 
        """
        template_fp = open(self.config.get(('email_log', 'template_file')), 'r')
        tpl = template_fp.read()
        template_fp.close()
        return tpl


class FileWatcherDaemon(Daemon):
    def __init__(self, pid_file):
        super(Daemon, self).__init__()
        self.config = None
        self.event_handler = None
        self.observer = None
        self.pid_file = pid_file

    def run(self):
        try:
            self.config = Config('./config/config.json')
        except FileNotFoundError:
            print('Could not find config file.')
            exit(1)
        self.observer = Observer()
        self.event_handler = EventHandler(self.config)
        self.observer.schedule(self.event_handler, self.config.get('pattern'), self.config.get('recursive'))
        self.observer.start()
        try:
            while True:
                time.sleep(1)
                self.tick()
        except KeyboardInterrupt:
            self.observer.stop()
            self.observer.join()
        exit(0)

    def tick(self):
        self.event_handler.cache_tick()
