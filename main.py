#!/usr/bin/env python
import os
import sys
import inspect
import cmd
import logging
import threading
import time
import struct
import fcntl
import termios
import signal
from ConfigParser import ConfigParser
from optparse import OptionParser
from datetime import datetime
import errno

cmd_args = sys.argv
cmd_file = inspect.getfile(inspect.currentframe())
cmd_dir = os.path.realpath(os.path.abspath(os.path.split(cmd_file)[0]))

lib_dir = os.path.join(cmd_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

import pexpect

orig_cwd = os.getcwd()
os.chdir(cmd_dir)


def restore_cwd():
    os.chdir(orig_cwd)

logging.basicConfig(filename='deploy_process.log',
                    format='%(asctime)s %(message)s',
                    level=logging.DEBUG)
log = logging.getLogger(__name__)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
log.addHandler(ch)


class DeployCmd(cmd.Cmd):

    def preloop(self):
        self.stages = dict()
        self.stage_nums = list()
        self.stages_done = dict()
        self.next_stage = 0
        self.cur_stage = None
        self.cur_status = None

        for root, dirs, files in os.walk(self.options.stages_dir):
            for stage_f_name in files:
                stage_name, stage_f_ext = os.path.splitext(stage_f_name)
                stage_action = stage_f_ext[1:]
                if stage_action in ('update', 'rollback'):
                    if stage_name not in self.stages:
                        self.stages[stage_name] = dict()
                    self.stages[stage_name][stage_action] = os.path.join(
                        root, stage_f_name)
        self.stage_nums = sorted(self.stages.keys())
        if os.path.exists('deploy_process.ini'):
            conf = ConfigParser()
            try:
                conf.read('deploy_process.ini')
                cur_stage_name = conf.get('position', 'current')
                if cur_stage_name in self.stage_nums:
                    self.cur_stage = self.stage_nums.index(cur_stage_name)
                    self.next_stage = self.cur_stage + 1
            except:
                log.warning('Broken deploy_process.ini file')
        self.update_prompt()
        if self.options.run:
            self.cmdqueue.append('continue')

    def update_prompt(self):
        if self.next_stage < len(self.stages):
            nxt = self.next_stage
        else:
            nxt = "None"
        self.prompt = "stage | cur: %s | next: %s > " % (self.cur_stage, nxt)

    def cmdloop(self, intro=None, options=None):
        self.options = options
        return cmd.Cmd.cmdloop(self, intro)

    def postloop(self):
        restore_cwd()

    def sigwinch_passthrough(self, sig, data):
        s = struct.pack("HHHH", 0, 0, 0, 0)
        a = struct.unpack('hhhh', fcntl.ioctl(sys.stdout.fileno(),
                          termios.TIOCGWINSZ, s))
        self.p.setwinsize(a[0], a[1])

    def apply_stage(self, action):
        if self.next_stage == len(self.stages):
            log.error("No next stages")
            self.cur_status = None
            return False
        else:
            stage = self.stages[self.stage_nums[self.next_stage]]
            if not stage.get(action):
                log.error('Stage %s has no %s action' % (
                    self.stage_nums[self.next_stage], action))
                return True
            time_init = datetime.now()
            logWrap = LogWrapper()
            env_fifo = EnvFIFO()
            try:
                self.p = pexpect.spawn(stage[action], logfile=logWrap,
                                       timeout=86400)
                signal.signal(signal.SIGWINCH, self.sigwinch_passthrough)
                self.p.interact()
                self.p.close()
            except OSError as e:
                if e.errno != errno.EIO:
                    raise e
            self.cur_status = self.p.exitstatus
            env_fifo.close()
            time_done = datetime.now()
            run_time = (time_done - time_init)
            log.info("Exit status: %s, run time: %s" % (self.cur_status,
                                                        run_time))
            return True

    def do_list(self, line):
        """ List all stages """
        for index, stage_name in enumerate(self.stage_nums):
            if index == self.cur_stage:
                comment = " (current stage)"
            elif index == self.next_stage:
                comment = " (next stage)"
            else:
                comment = ""
            log.info("Stage %i: %s%s" % (index, stage_name, comment))

    def write_stage(self):
        if self.cur_stage is not None:
            with open('deploy_process.ini', 'w') as f:
                conf = ConfigParser()
                conf.add_section('position')
                conf.set('position', 'current',
                         self.stage_nums[self.cur_stage])
                conf.write(f)
        elif os.path.exists('deploy_process.ini'):
            os.unlink('deploy_process.ini')

    def reload_deploy(self):
        global cmd_file
        global cmd_args
        if os.environ.get('RELOAD_DEPLOY'):
            del os.environ['RELOAD_DEPLOY']
            log.info('Restarting...')
            run_args = ['python', cmd_file]
            run_args.extend(cmd_args[1:])
            run_string = ' '.join(run_args)
            logging.shutdown()
            restore_cwd()
            os.execlp('bash', 'bash', '-c', run_string)

    def do_continue(self, line):
        """ Run while exit status is good """
        global cmd_args
        if '-r' not in cmd_args:
            cmd_args.append('-r')
        self.cur_status = 0
        while self.cur_status == 0:
            self.do_do(line)
        self.update_prompt()
        if self.next_stage == len(self.stages):
            if os.path.exists('deploy_process.ini'):
                os.unlink('deploy_process.ini')
            return True

    def do_do(self, line):
        """ Apply next stage """
        if self.apply_stage('update'):
            self.cur_stage = self.next_stage
            self.next_stage = self.next_stage + 1
            self.write_stage()
            self.reload_deploy()

    def do_retry(self, line):
        """ Apply current stage again """
        if self.cur_stage is not None:
            self.next_stage = self.cur_stage
            self.do_do(line)

    def do_undo(self, line):
        """ Apply current stage rollback """
        if self.cur_stage is not None:
            self.next_stage = self.cur_stage
            self.apply_stage('rollback')
            if self.cur_stage > 0:
                self.cur_stage = self.cur_stage - 1
            else:
                self.cur_stage = None
            self.write_stage()
            self.reload_deploy()

    def do_goto(self, line):
        """ Go to specified stage """
        if not line.isdigit():
            log.info("Usage: goto number_of_stage")
            return
        stage_num = int(line)
        if stage_num in range(0, len(self.stages)):
            self.next_stage = stage_num
            self.do_do('')
        else:
            log.error('No such stage')

    def do_EOF(self, line):
        """Exit program"""
        return True

    def do_exit(self, line):
        """Exit program"""
        return True

    def completenames(self, text, *ignored):
        names = ['continue', 'do', 'undo', 'retry', 'list', 'exit',
                 'goto', 'help']
        return [a for a in names if a.startswith(text)]

    def emptyline(self):
        """Do nothing on empty input line"""
        pass

    def precmd(self, line):
        if line != '':
            if line == 'EOF':
                log.info('exit')
            else:
                log.info(line)
        return cmd.Cmd.precmd(self, line)

    def postcmd(self, stop, line):
        self.update_prompt()
        return cmd.Cmd.postcmd(self, stop, line)


class LogWrapper():

    def __init__(self):
        """Setup the file-like object with a logger and a loglevel
        """
        self.logger = logging.getLogger('LogWrapper')
        self.level = logging.DEBUG
        self.partline = ''

    def write(self, lines):
        for line in lines.splitlines(True):
            self.partline += line
            if self.partline[-1] in ('\r', '\n'):
                self.logger.log(self.level, self.partline.strip())
                self.partline = ''

    def flush(self):
        pass


class EnvFIFO(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        if os.path.exists('/tmp/deploy.cmd'):
            os.unlink('/tmp/deploy.cmd')
        os.mkfifo('/tmp/deploy.cmd')
        fifo_fd = os.open('/tmp/deploy.cmd', os.O_RDONLY | os.O_NONBLOCK)
        self.fifo = os.fdopen(fifo_fd, 'r', 0)
        self.done = False
        self.start()

    def read_fifo(self):
        try:
            for line in iter(self.fifo.readline, ''):
                env_s = [s.strip() for s in line.split('=', 1)]
                k, v = env_s[0], '1'
                if len(env_s) > 1:
                    v = env_s[1]
                if env_s[0] in ('RELOAD_DEPLOY', 'BASH_ENV'):
                    os.environ[k] = v
        except IOError as e:
            if e.errno != errno.EAGAIN:
                raise e

    def run(self):
        while not self.done:
            self.read_fifo()
            time.sleep(0.5)

    def close(self):
        self.done = True
        self.read_fifo()
        self.fifo.close()
        os.unlink('/tmp/deploy.cmd')


if __name__ == '__main__':
    optionparser = OptionParser(usage="usage: %prog [options]")
    optionparser.add_option("-s", "--stages-dir", dest="stages_dir",
                            default="./stages",
                            help="stages root directory [ default: %default ]")
    optionparser.add_option("-r", "--run", action="store_true", dest="run",
                            default=False,
                            help="run all stages while stage exit status is 0,\
                            exit after all done stages")
    (options, args) = optionparser.parse_args()
    try:
        DeployCmd().cmdloop(options=options)
    except KeyboardInterrupt:
        log.info("exit")
