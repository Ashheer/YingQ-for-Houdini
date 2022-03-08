from __future__ import print_function
from __future__ import absolute_import
#
# Copyright (c) <2020> Side Effects Software Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# NAME:	        pdgjobcmd.py ( Python )
#
# COMMENTS:     Wrapper script for executing PDG commands.  This script is invoked
#               by the farm scheduler clients / blades.  It handles pre and post
#               processing as well executing the command.
#

import argparse
from base64 import urlsafe_b64decode
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
pdgcmd = None

eExitCodeError, eExitCodeWarn, eExitCodeRetry, eExitCodeIgnore = range(4)

proc = None

def main():
    parser = argparse.ArgumentParser(description="""
Wrapper for PDG commands sent by PDG.
""")
    parser.add_argument('--setenv', nargs=2, default=[], action='append',
                        help='Environment variable settings KEY VAL, & means append or prepend to existing')
    parser.add_argument('--preshell', type=str,
                        help='Shell script to be executed/sourced before command is executed')
    parser.add_argument('--postshell', type=str,
                        help='Shell script to be executed/sourced after command is executed')
    parser.add_argument('--prepy', type=str,
                        help='Python script to be execd in wrapper script before command process is spawned')
    parser.add_argument('--postpy', type=str,
                        help='Python script to be execd in wrapper script after command process exits')
    parser.add_argument('--norpc', action='store_true',
                        help='Dont send any RPC callbacks to PDG')
    parser.add_argument('--keepalive', type=int, default=0,
                        help='Seconds between RPC heartbeats, 0 to disable')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sendstatus', action='store_true',
                        help='Send either the failed or success RPC when work is finished')
    group.add_argument('--sendsuccess', action='store_true',
                        help='Send the success RPC, but not the failed')   
    parser.add_argument('--echandleby', type=int,
                        help='How to handle non-zero exit codes [Error/Warning/Retry/Ignore]')
    parser.add_argument('--eccustomcode', type=int,
                        help='The specific exit code that --echandleby applies to')
    parser.add_argument('--pswait', type=float,
                        help='Number of seconds to wait after success RPC, before exiting')

    parser.add_argument('cmd', nargs=argparse.REMAINDER,
                        help='The program and args to be executed [REQUIRED]')

    is_windows = sys.platform.startswith('win')

    args = parser.parse_args()

    for setk, setv in args.setenv:
        os.environ[setk] = setv

    # Decode PDG_PATHMAP if needed
    if 'PDG_PATHMAP_B64' in os.environ:
        s = os.environ.pop('PDG_PATHMAP_B64')
        mod_4 = len(s) % 4
        if mod_4:
            s += '=' * (4 - mod_4)

        if sys.version_info.major >= 3:
            path_map = urlsafe_b64decode(s.encode()).decode()
        else:
            path_map = urlsafe_b64decode(s)
        os.environ['PDG_PATHMAP'] = path_map

    # We set some paths in the environment, which in order to support multiple
    # platforms, may be based on another env var like $HQROOT
    for var in ('PDG_TEMP', 'PDG_DIR', 'PDG_SCRIPTDIR'):
        if var in os.environ:
            os.environ[var] = os.path.expandvars(os.environ[var])

    # if we've been given HOUDINI_PATH we append it as well as PDG_TEMP
    if 'PDG_TEMP' in os.environ:
        houdini_path = os.environ['PDG_TEMP']
        if 'HOUDINI_PATH' in os.environ:
            hpath_env = os.environ['HOUDINI_PATH']
            houdini_path += ';' + hpath_env
        os.environ['HOUDINI_PATH'] = houdini_path + ';&'

    # use the .exe extension marker to map our vargs
    if is_windows:
        os.environ['PDG_EXE'] = '.exe'
    else:
        os.environ['PDG_EXE'] = ''

    if args.keepalive and args.norpc:
        print('Warning: --keepalive has been disabled by --norpc')
    
    # We have to be careful when we import pdgcmd, because it builds some module state
    # which depends on the environment, which we have just finished updating
    global pdgcmd
    try:
        import pdgcmd
    except ImportError:
        import pdgjob.pdgcmd as pdgcmd

    pdgcmd.verboseAnnounce()

    vargs = list(map(pdgcmd.localizePath, args.cmd))
    del os.environ['PDG_EXE']

    workitem_id = pdgcmd.getItemIdFromEnv()

    if not args.norpc:
        pdgcmd.workItemStartCook(workitem_id)

    # reconstruct the tail of our vargs by quoting anything with whitespace
    shell_cmd = ' '.join([('"%s"' % tok) if re.search(r'\s', tok) else tok for tok in vargs])

    if is_windows:
        # replace any $VAR with %VAR%
        shell_cmd = re.sub(r'\$(\w+)', r'%\1%', shell_cmd)
        shell_prefix = ""
    else:
        shell_prefix = ""

    if args.preshell:
        # expand
        farg = __check_filearg(args.preshell, 'preshell', parser)
        if is_windows:
            shell_cmd = 'call ' + farg + ' && ' + shell_cmd
        else:
            shell_cmd = '. ' + farg + ' && ' + shell_cmd

    if args.postshell:
        # do not expand the postshell argument because it might use envvars
        # set up in prior execution
        farg = args.postshell
        if is_windows:
            shell_cmd += ' && call ' + farg
        else:
            shell_cmd += ' && . ' + farg

    if shell_prefix:
        shell_cmd = shell_prefix + shell_cmd

    def _spawnProc():
        global proc
        print('*** Starting:\n' + shell_cmd + '\n')
        sys.stdout.flush()

        if args.prepy:
            __execScript(args.prepy, 'prepy', parser)

        # Explicitly pass `env` to
        proc = subprocess.Popen(shell_cmd, stdin=subprocess.PIPE,
                                shell=True, env=os.environ)
        # Avoid inheriting stdin to avoid python bug on Windows 7
        # https://bugs.python.org/issue3905
        proc.stdin.close()

        # Install signal handlers so we can cleanup our child process
        signal.signal(signal.SIGINT, __signal_handler)
        signal.signal(signal.SIGTERM, __signal_handler)

        keepalive_period = max(args.keepalive, 0)
        if keepalive_period and not args.norpc:
            # send keepalive RPCs while the job is executing
            stopped = threading.Event()
            def keepalive_tick():
                while not stopped.wait(keepalive_period):
                    pdgcmd.keepalive(workitem_id)
            keepalive_thread = threading.Thread(target=keepalive_tick)
            keepalive_thread.daemon = True
            keepalive_thread.start()

            exit_code = proc.wait()
            # interrupt the keepalive wait
            stopped.set()
            keepalive_thread.join()
        else:
            exit_code = proc.wait()

        if args.postpy:
            __execScript(args.postpy, 'postpy', parser)

        wrapper_exit_code = __checkExitCode(exit_code, args)

        if wrapper_exit_code is None:
            wrapper_exit_code = _spawnProc()

        return wrapper_exit_code

    exit_code = _spawnProc()
    job_failed = exit_code != 0

    if job_failed:
        ec = str(exit_code)

        if not is_windows:
            # Handle exit codes that are returned as 128 + signal_num
            if exit_code > 128:
                exit_code = exit_code - 128

            # Handle exit codes that are returned as -signal_num
            if exit_code < 0:
                exit_code = -exit_code

            # Check for the signal name in the list of known signals
            for sig_name, code in signal.__dict__.items():
                if code == exit_code:
                    ec = "{} ({})".format(sig_name, exit_code)

        print("\n** Finished with Exit Code: {}".format(ec))
    
    if (args.sendstatus or args.sendsuccess) and not args.norpc:
        # Send the final status RPC
        if job_failed:
            if args.sendstatus:
                pdgcmd.workItemFailed(workitem_id)
        else:
            pdgcmd.workItemSuccess(workitem_id)
            if args.pswait:
                time.sleep(args.pswait)

    sys.exit(exit_code)

def __checkExitCode(exit_code, args):
    # Checks for exit-code conditions and returns the value that should be
    # returned from the wrapper script, returning None if a retry is indicated
    wrapper_exit_code = exit_code
    if exit_code > 0:
        exit_code_custom = args.eccustomcode
        exit_code_handling = args.echandleby

        if exit_code_custom is None or exit_code_custom == exit_code:
            if exit_code_handling == eExitCodeIgnore:
                wrapper_exit_code = 0
            elif exit_code_handling == eExitCodeWarn:
                msg = 'Finished with Exit Code {}'.format(exit_code)
                print('\n*** WARNING: ' + msg)
                if not args.norpc:
                    pdgcmd.warning(msg)
                wrapper_exit_code = 0
            elif exit_code_handling == eExitCodeRetry:
                print("\n*** Exit Code = {}.  Restarting\n".format(
                    exit_code))
                # recursive call to retry
                wrapper_exit_code = None
    return wrapper_exit_code

def __check_filearg(farg, argname, parser):
    farg = os.path.expandvars(farg)
    if not os.path.exists(farg):
        print("error: argument --{}: can't open {}".format(argname, farg))
        parser.print_usage()
        sys.exit(1)
    return farg

def __execScript(pyscript, name, parser):
    with open(__check_filearg(pyscript, name, parser)) as fin:
        exec(fin, globals(), locals())

def __signal_handler(sig_num, stack):
    if proc and proc.poll() is None:
        print("Caught Signal {}, signalling child {}".format(sig_num, proc.pid))
        sys.stdout.flush()
        proc.send_signal(sig_num)

if __name__ == "__main__":
    main()
