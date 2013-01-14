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
import pexpect
import pwd
import termcolor
from ConfigParser import ConfigParser, Error as ConfigParserError
from optparse import OptionParser
from datetime import datetime
import errno

cmd_args = sys.argv
cmd_file = inspect.getfile(inspect.currentframe())
log = logging.getLogger(__name__)

tty_path = None
tty_owner = None


class DeployCmd(cmd.Cmd):

    def preloop(self):
        self.stages = dict()
        self.stage_nums = list()
        self.stages_done = dict()
        self.next_stage = 0
        self.cur_stage = None
        self.cur_status = None
        global tty_path
        global tty_owner
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

        if os.path.exists(self.options.process_file):
            conf = ConfigParser()
            try:
                conf.read(self.options.process_file)
                cur_stage_name = conf.get('position', 'current')
                if cur_stage_name in self.stage_nums:
                    self.cur_stage = self.stage_nums.index(cur_stage_name)
                    self.next_stage = self.cur_stage + 1
                prev_tty_path = conf.get('tty', 'path')
                prev_tty_owner = conf.get('tty', 'owner')
                if tty_owner != prev_tty_owner:
                    log.error('n3d deploy process has already started by '
                              'the user %s on terminal %s'
                              % (prev_tty_owner, prev_tty_path))
                    log.error('If you still want to continue as this user,'
                              'change the TTY OWNER in: %s, by example:\n'
                              'sed -i "s/%s/%s/" %s'
                              % (self.options.process_file, prev_tty_owner,
                                 tty_owner, self.options.process_file))
                    sys.exit(1)
            except ConfigParserError:
                log.warning('Broken deploy_process.ini file')
        self.update_prompt()
        if self.options.run:
            self.cmdqueue.append('continue')

    def stage_name(self, stage):
        if stage is not None and stage >= 0 and stage < len(self.stages):
            return self.stage_nums[stage].split('-', 1)[-1]
        else:
            return None

    def stage_colored(self, stage):
        stage_name = self.stage_name(stage)
        if stage_name is not None:
            return "%s %s" % (
                readline_colored(stage, 'green'),
                readline_colored(stage_name, 'white'))

    def update_prompt(self):
        self.prompt = "stage | cur: %s | next: %s > " % (
                      self.stage_colored(self.cur_stage),
                      self.stage_colored(self.next_stage))

    def cmdloop(self, intro=None, options=None):
        self.options = options
        return cmd.Cmd.cmdloop(self, intro)

    def sigwinch_passthrough(self, sig, data):
        if 'TIOCGWINSZ' in dir(termios):
            TIOCGWINSZ = termios.TIOCGWINSZ
        else:
            TIOCGWINSZ = 1074295912
        s = struct.pack("HHHH", 0, 0, 0, 0)
        a = struct.unpack('hhhh', fcntl.ioctl(sys.stdout.fileno(),
                          TIOCGWINSZ, s))
        self.p.setwinsize(a[0], a[1])

    def pexpect_filter(self, line):
        stage_name = self.stage_name(self.next_stage)
        if stage_name is not None:
            result = stage_name + ' : ' + line
        else:
            result = line
        if line[-1] not in ('\r', '\n'):
            result += '\r\n'
        return result

    def apply_stage(self, action):
        if self.next_stage == len(self.stages):
            log.error("Finished all stages")
            self.cur_status = None
            return False
        else:
            stage = self.stages[self.stage_nums[self.next_stage]]
            if not stage.get(action):
                log.error('Stage %s has no %s action' % (
                    self.stage_name(self.next_stage), action))
                return True
            oldcwd = os.getcwd()
            os.chdir(self.options.work_dir)
            if os.path.exists('deploy/stage.lock'):
                with open('deploy/stage.lock', 'r') as f:
                    run_stage = f.read()
                    log.error('Stage %s is already running' % run_stage)
                    return False
            f = open('deploy/stage.lock', 'w')
            f.write('%s on %s' % (self.stage_name(self.next_stage), tty_path))
            f.close()
            time_init = datetime.now()
            logWrap = LogWrapper()
            env_fifo = EnvFIFO()
            try:
                self.p = pexpect.spawn(stage[action], logfile=logWrap,
                                       timeout=86400)
                signal.signal(signal.SIGWINCH, self.sigwinch_passthrough)
                self.p.interact(output_filter=self.pexpect_filter)
            except OSError as e:
                if e.errno != errno.EIO:
                    raise e
            self.p.close()
            self.cur_status = self.p.exitstatus
            env_fifo.close()
            os.unlink('deploy/stage.lock')
            os.chdir(oldcwd)
            time_done = datetime.now()
            run_time = (time_done - time_init)
            exit_log = "%s exit status: %s, run time: %s" % (
                       self.stage_name(self.next_stage),
                       self.cur_status,
                       run_time)
            if self.cur_status is not None and int(self.cur_status) == 0:
                log.info(exit_log)
            else:
                log.error(exit_log)
            log.info('\n\n\n')
            return True

    def do_list(self, line):
        """ List all stages """
        for index, stage_name in enumerate(self.stage_nums):
            if index == self.cur_stage:
                comment = "(current stage)"
                stage_marker = '*'
            elif index == self.next_stage:
                comment = "(next stage)"
                stage_marker = '>'
            else:
                comment = ""
                stage_marker = ' '
            log.info("%s%2i: %s %s" % (stage_marker, index, stage_name,
                                       comment))

    def write_stage(self):
        if self.cur_stage is not None:
            with open(self.options.process_file, 'w') as f:
                conf = ConfigParser()
                conf.add_section('position')
                conf.set('position', 'current',
                         self.stage_nums[self.cur_stage])
                conf.add_section('tty')
                conf.set('tty', 'path', tty_path)
                conf.set('tty', 'owner', tty_owner)
                conf.write(f)
        elif os.path.exists(self.options.process_file):
            os.unlink(self.options.process_file)

    def reload_deploy(self):
        global cmd_file
        global cmd_args
        if os.environ.get('RELOAD_DEPLOY'):
            del os.environ['RELOAD_DEPLOY']
            log.warning('Restarting...')
            run_args = ['python', cmd_file]
            run_args.extend(cmd_args[1:])
            run_string = ' '.join(run_args)
            logging.shutdown()
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
            if os.path.exists(self.options.process_file):
                os.unlink(self.options.process_file)
            return True

    def do_do(self, line):
        """ Apply next or specified stage. Usage: do [number_of_stage] """
        if line != '':
            if not line.isdigit():
                log.info("Usage: do [number_of_stage]")
                return False
            stage_num = int(line)
            if stage_num in range(0, len(self.stages)):
                self.next_stage = stage_num
            else:
                log.error('No such stage')
                return False

        if self.apply_stage('update'):
            self.cur_stage = self.next_stage
            self.next_stage = self.next_stage + 1
            self.write_stage()
            self.reload_deploy()

    def do_retry(self, line):
        """ Apply current stage again """
        if self.cur_stage is not None:
            self.next_stage = self.cur_stage
            self.do_do('')

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

    def do_EOF(self, line):
        """Exit program"""
        return True

    def do_exit(self, line):
        """Exit program"""
        return True

    def completenames(self, text, *ignored):
        names = ['continue', 'do', 'undo', 'retry', 'list', 'exit',
                 'help']
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
            if self.partline[-1] == '\n':
                self.logger.log(self.level, self.partline.strip())
            if self.partline[-1] in ('\r', '\n'):
                self.lastline = self.partline
                self.partline = ''
        self.logger.log(self.level, self.lastline.strip())

    def flush(self):
        pass


def set_env(line):
    if not line or not line.strip():
        return
    k, v = [s.strip() for s in line.partition('=')[::2]]
    v = v or '1'
    log.info("New ENV variable: %s=%s" % (k, v))
    os.environ[k] = v


class EnvFIFO(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.fifo_name = 'deploy/deploy.cmd'
        if os.path.exists(self.fifo_name):
            os.unlink(self.fifo_name)
        os.mkfifo(self.fifo_name)
        fifo_fd = os.open(self.fifo_name, os.O_RDONLY | os.O_NONBLOCK)
        self.fifo = os.fdopen(fifo_fd, 'r', 0)
        self.done = False
        self.start()

    def read_fifo(self):
        try:
            for line in iter(self.fifo.readline, ''):
                set_env(line)
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
        os.unlink(self.fifo_name)


def readline_colored(text, color=None, on_color=None, attrs=None):
    if os.getenv('ANSI_COLORS_DISABLED') is None:
        fmt_str = '\001\033[%dm\002%s'
        if color is not None:
            text = fmt_str % (termcolor.COLORS[color], text)

        if on_color is not None:
            text = fmt_str % (termcolor.HIGHLIGHTS[on_color], text)

        if attrs is not None:
            for attr in attrs:
                text = fmt_str % (termcolor.ATTRIBUTES[attr], text)

        text += '\001\033[0m\002'
    return text


class ColoredFormatter(logging.Formatter):

    colors = {
        'WARNING': 'yellow',
        'INFO': 'green',
        'DEBUG': 'blue',
        'CRITICAL': 'yellow',
        'ERROR': 'red'
    }

    def format(self, record):
        result = logging.Formatter.format(self, record)
        if result is not None:
            return termcolor.colored(result, self.colors[record.levelname])


def main():
    global tty_path
    global tty_owner
    optionparser = OptionParser(usage="usage: %prog [options]")
    optionparser.add_option("-s", "--stages-dir", dest="stages_dir",
                            default=os.path.join("deploy", "stages"),
                            help="stages root directory [ default: %default ]")
    optionparser.add_option("-w", "--work-dir", dest="work_dir",
                            default=os.getcwd(),
                            help="working directory [ current: %default ]")
    optionparser.add_option("-l", "--log-file", dest="log_file",
                            default=os.path.join("deploy",
                                                 "deploy_process.log"),
                            help="log file [ default: %default ]")
    optionparser.add_option("-p", "--process-file", dest="process_file",
                            default=os.path.join("deploy",
                                                 "deploy_process.ini"),
                            help="The file containing the current stage of the\
                            deployment process [ default: %default ]")
    optionparser.add_option("-E", "--env", action="append", dest="envs",
                            help="Add environment variable for stages")
    optionparser.add_option("-c", "--envvars", dest="envvars",
                            default=os.path.join("deploy",
                                                 "envvars"),
                            help="file with environment variables\
                            in KEY=value format [ default: %default ]")
    optionparser.add_option("-r", "--run", action="store_true", dest="run",
                            default=False,
                            help="run all stages while stage exit status is 0,\
                            exit after all done stages")
    (options, args) = optionparser.parse_args()
    if not os.path.exists(options.stages_dir):
        print "Stages directory not found: %s" % options.stages_dir
        sys.exit(1)
    if not os.path.exists(options.work_dir):
        print "Working directory not found: %s" % options.work_dir
        sys.exit(1)
    if sys.stdin.isatty():
        tty_path = os.ttyname(sys.stdin.fileno())
        tty_owner = pwd.getpwuid(os.stat(tty_path).st_uid).pw_name
    else:
        print "You must have a TTY"
        sys.exit(1)
    logging.basicConfig(filename=options.log_file,
                        format='%(asctime)s (' + tty_owner + ') %(message)s',
                        level=logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(ColoredFormatter())
    log.addHandler(ch)
    if os.path.exists(options.envvars):
        with open(options.envvars, 'r') as f:
            for line in f:
                line = line.split('#', 1)[0]
                set_env(line)
    if options.envs:
        for line in options.envs:
            set_env(line)
    try:
        DeployCmd().cmdloop(options=options)
    except KeyboardInterrupt:
        log.info("exit")
        sys.exit(1)


if __name__ == '__main__':
    main()
