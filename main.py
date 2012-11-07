#!/usr/bin/env python
import os, sys, cmd
from ConfigParser import ConfigParser
from optparse import OptionParser
from shell_command import ShellCommand 
import logging
import threading
from datetime import datetime

logging.basicConfig(filename='deploy_process.log', format='%(asctime)s %(message)s', level = logging.DEBUG)
log = logging.getLogger(__name__)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
log.addHandler(ch)

class DeployCmd(cmd.Cmd):

    stages = dict()
    stage_nums = []
    stages_done = dict()
    next_stage = 0
    cur_stage = None
    cur_status = None

    def update_prompt(self):
        if self.next_stage < len(self.stages):
            nxt = self.next_stage
        else:
            nxt = "None"
        self.prompt = "stage | cur: %s | next: %s > " % (self.cur_stage, nxt)

    def cmdloop(self, intro=None, options=None):
        self.options = options
        return cmd.Cmd.cmdloop(self, intro)

    def preloop(self):
        for root, dirs, files in os.walk(self.options.stages_dir):
            for stage_f_name in files:
                stage_name, stage_f_ext = os.path.splitext(stage_f_name)
                stage_action = stage_f_ext[1:]
                if stage_action in ('update', 'rollback'):
                    if stage_name not in self.stages:
                        self.stages[stage_name] = dict()
                    self.stages[stage_name][stage_action] = \
                                            os.path.join(root,stage_f_name)
        self.stage_nums = sorted(self.stages.keys())
        if os.path.exists('deploy_process.ini'):
            conf = ConfigParser()
	    conf.read('deploy_process.ini')
            self.cur_stage = int(conf.get('position','current'))
            self.next_stage = self.cur_stage+1
            if self.cur_stage < 0:
                self.cur_stage = None
        self.update_prompt()
        if self.options.run:
            self.cmdqueue.append('continue')

    def apply_stage(self, action):
        if self.next_stage == len(self.stages):
            log.error("No next stages")
            self.cur_status = None
            return False
        else:
            stage = self.stages[self.stage_nums[self.next_stage]]
            time_init = datetime.now()
            logWrap = LogWrapper(log, logging.INFO)
            self.cur_status = \
      ShellCommand(stage[action], stdout=logWrap, stderr=logWrap).shell_call()
            logWrap.close()
            time_done = datetime.now()
            run_time = (time_done - time_init)
            log.info("Exit status: %s, run time: %s" % \
                             (self.cur_status, run_time ) )
            return True

    def do_list(self,line):
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
            with open('deploy_process.ini','w') as f:
                conf = ConfigParser()
                conf.add_section('position')
                conf.set('position', 'current', self.cur_stage)
                conf.write(f)
        elif os.path.exists('deploy_process.ini'):
            os.unlink('deploy_process.ini')

    def do_continue(self, line):
        """ Run while exit status is good """
        self.cur_status = 0
        while self.cur_status == 0:
            self.do_next(line)
        self.update_prompt()
        if self.next_stage == len(self.stages) and self.options.run:
            if os.path.exists('deploy_process.ini'):
                os.unlink('deploy_process.ini')
            return True

    def do_next(self, line):
        """ Apply next stage """
        if self.apply_stage('update'):
            self.cur_stage=self.next_stage
            self.next_stage=self.next_stage+1
            self.write_stage()

    def do_tryagain(self, line):
        """ Apply current stage again """
        if self.cur_stage is not None:
            self.next_stage=self.cur_stage
            self.do_next(line)

    def do_rollback(self, line):
        """ Apply last stage rollback """
        if self.cur_stage is not None:
            self.next_stage=self.cur_stage
            self.apply_stage('rollback')
            if self.cur_stage > 0:
                self.cur_stage = self.cur_stage-1
            else:
                self.cur_stage = None
            self.write_stage()

    def do_goto(self, line):
        """ Go to specified stage """
        stage_num = int(line)
        if stage_num in range(0, len(self.stages)):
            self.next_stage=stage_num
            self.do_next('')
        else:
            log.error('No such stage')

    def completenames(self, text, *ignored):
        names = ['continue','next','rollback','tryagain','list','exit','goto','help']
        return [a for a in names if a.startswith(text)]

    def do_EOF(self, line):
        print('^D')
        return True

    def do_exit(self, line):
        return True
 
    def precmd(self,line):
        if line != '':
            log.info("The command is: %s", line)
        return cmd.Cmd.precmd(self, line)
 
    def postcmd(self,stop,line):
        self.update_prompt()
        return cmd.Cmd.postcmd(self, stop, line)

class LogWrapper(threading.Thread):

    def __init__(self, logger, level):
        """Setup the object with a logger and a loglevel
        and start the thread
        """
        threading.Thread.__init__(self)
        self.daemon = False
        self.logger = logger
        self.level = level
        self.fdRead, self.fdWrite = os.pipe()
        self.pipeReader = os.fdopen(self.fdRead)
        self.start()

    def fileno(self):
        """Return the write file descriptor of the pipe
        """
        return self.fdWrite

    def run(self):
        """Run the thread, logging everything.
        """
        for line in iter(self.pipeReader.readline, ''):
            self.logger.log(self.level, line.strip('\n'))

        self.pipeReader.close()

    def close(self):
        """Close the write end of the pipe.
        """
        os.close(self.fdWrite)

if __name__ == '__main__':
    optionparser = OptionParser(usage="usage: %prog [options]")
    optionparser.add_option("-s", "--stages-dir", dest="stages_dir",
                            default="./stages",
                            help="stages root directory [ default: %default ]")
    optionparser.add_option("-r", "--run", action="store_true", dest="run",\
                            default=False,
                            help="run all stages while stage exit status is 0,\
                            exit after all done stages")
    (options, args) = optionparser.parse_args()
    DeployCmd().cmdloop(options=options)

