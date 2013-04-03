# Copyright (c) 2013 Ian C. Good
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

from __future__ import absolute_import

import sys
import os
import os.path
import warnings

from config import Config, ConfigError
import slimta.system

from .celery import get_app as get_celery_app


class SlimtaState(object):

    _global_config_files = [os.path.expanduser('~/.slimta.conf'),
                            '/etc/slimta.conf']

    def __init__(self, attached=True):
        self.config_file = os.getenv('SLIMTA_CONFIG', None)
        self.attached = attached
        self.ap = None
        self.cfg = None
        self.edges = {}
        self.queues = {}
        self.relays = {}
        self._celery = None

    def _check_configs(self, files):
        for config_file in files:
            config_file = os.path.expanduser(config_file)
            f = None
            try:
                f = open(config_file, 'r')
            except IOError:
                pass
            else:
                return Config(f)
            finally:
                if f is not None:
                    f.close()
        return None

    def load_config(self, config_file):
        if self.cfg:
            return True

        files = self._global_config_files
        if config_file:
            files = [config_file]

        self.cfg = self._check_configs(files)
        return bool(self.cfg)

    def drop_privileges(self):
        if os.getuid() == 0:
            user = self.cfg.process.get('user')
            group = self.cfg.process.get('group')
            slimta.system.drop_privileges(user, group)
        else:
            warnings.warn('Only superuser can drop privileges.')

    def redirect_streams(self):
        flag = self.cfg.process.get('daemon', False)
        if flag and not self.attached:
            so = self.cfg.process.get('stdout')
            se = self.cfg.process.get('stderr')
            si = self.cfg.process.get('stdin')
            slimta.system.redirect_stdio(so, se, si)

    def daemonize(self):
        flag = self.cfg.process.get('daemon', False)
        if flag and not self.attached:
            slimta.system.daemonize()

    def _start_relay(self, name, options=None):
        if name in self.relays:
            return self.relays[name]
        if not options:
            options = getattr(self.cfg.relay, name)
        new_relay = None
        if options.type == 'mx':
            from slimta.relay.smtp.mx import MxSmtpRelay
            new_relay = MxSmtpRelay()
        elif options.type == 'static':
            from slimta.relay.smtp.static import StaticSmtpRelay
            host = options.host
            port = options.get('port', 25)
            new_relay = StaticSmtpRelay(host, port)
        elif options.type == 'maildrop':
            from slimta.maildroprelay import MaildropRelay
            executable = options.get('executable')
            new_relay = MaildropRelay(executable=executable)
        else:
            raise ConfigError('relay type does not exist: '+options.type)
        self.relays[name] = new_relay
        return new_relay

    def _start_queue(self, name, options=None):
        if name in self.queues:
            return self.queues[name]
        if not options:
            options = getattr(self.cfg.queue, name)
        new_queue = None
        if not options or options.get('type', 'default') == 'default':
            from slimta.queue import Queue
            pass
        elif options.type == 'celery':
            from slimta.celeryqueue import CeleryQueue
            relay_name = options.get('relay')
            if not relay_name:
                raise ConfigError('queue sections must be given a relay name')
            relay = self._start_relay(relay_name)
            new_queue = CeleryQueue(self.celery, relay, name)
        else:
            raise ConfigError('queue type does not exist: '+options.type)
        self.queues[name] = new_queue
        return new_queue

    @property
    def celery(self):
        if not self._celery:
            self._celery = get_celery_app(self.cfg)
        return self._celery

    def start_celery_queues(self):
        for name, options in dict(self.cfg.queue).items():
            if options.type == 'celery':
                self._start_queue(name, options)

    def _start_edge(self, name, options=None):
        if name in self.edges:
            return self.edges[name]
        if not options:
            options = getattr(self.cfg.edge, name)
        new_edge = None
        if options.type == 'smtp':
            from slimta.edge.smtp import SmtpEdge
            ip = options.listeners[0].get('interface', '127.0.0.1')
            port = int(options.listeners[0].get('port', 25))
            queue_name = options.get('queue')
            if not queue_name:
                raise ConfigError('edge sections must be given a queue name')
            queue = self._start_queue(queue_name)
            new_edge = SmtpEdge((ip, port), queue)
            new_edge.start()
        else:
            raise ConfigError('edge type does not exist: '+options.type)
        self.edges[name] = new_edge
        return new_edge

    def start_edges(self):
        for name, options in dict(self.cfg.edge).items():
            self._start_edge(name, options)

    def worker_loop(self):
        try:
            self.celery.Worker().run()
        except (KeyboardInterrupt, SystemExit):
            print

    def loop(self):
        from gevent.event import Event
        try:
            Event().wait()
        except (KeyboardInterrupt, SystemExit):
            print


# vim:et:fdm=marker:sts=4:sw=4:ts=4
