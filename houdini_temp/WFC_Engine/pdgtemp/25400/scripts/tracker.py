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
# NAME:	        tracker.py ( Python )
#
# COMMENTS:     Wrapper script for running Houdini's sim tracker.
#
#               Note: This module is intended to not have any dependency on a
#               Houdini installation. The simtracker.py can be copied from
#               Houdini and made available to this script without needing to
#               run this script using Hython.

from __future__ import print_function, absolute_import

import sys
import optparse
import os
import socket
import threading
import simtracker
import struct

try:
    from pdgcmd import reportResultData, PDGPingHelper, printlog
    from pdgcmd import setStringAttrib, setIntAttrib
    from pdgjson import *
except:
    from pdgjob.pdgcmd import reportResultData, PDGPingHelper, printlog
    from pdgcmd.pdgcmd import setStringAttrib, setIntAttrib
    from pdgjob.pdgjson import *

class SimTrackerThread(threading.Thread):
    def __init__(self, *args, **kwargs):

        # Python 3 of marking the thread as daemonic.
        # Python exits when there are only daemonic threads left.
        if sys.version_info.major >= 3:
            kwargs["daemon"] = True

        super(SimTrackerThread, self).__init__(*args, **kwargs)

    # Python 2 way of marking the thread as daemonic.
    def _set_daemon(self):
        return True

def initTracker(port, webport, verbosity):
    # First spawn the tracker and set the verbosity
    simtracker.setVerbosity(verbosity)
    tracker_thread = SimTrackerThread(target=lambda: simtracker.serve(port, webport))
    tracker_thread.start()

    # Wait for the tracker to be ready and get the host and ports.
    simtracker.waitForListener()
    tracker_host = os.environ.get("PDG_TRACKER_HOST", socket.getfqdn())
    tracker_port = simtracker.getListenPort()
    web_port = simtracker.getWebPort()

    web_link = "http://{}:{}".format(tracker_host, web_port)

    # Output some useful information.
    print("")
    print("Tracker listening on port {}".format(tracker_port))
    print("To view simulation statistics, go to {}".format(web_link))
    print("")

    # Report the host/ip of the tracker itself
    setStringAttrib("trackerhost", tracker_host, 0)
    setIntAttrib("trackerport", tracker_port, 0)

    # Report the tracker link as a work item result
    reportResultData(web_link, result_data_tag="tracker/webpage", and_success=True)

    # Flush printed text to stdout out.  This function will be executed from
    # Python instead of hython which typically buffers its output.
    sys.stdout.flush()

    # Wait for the tracker to finish.
    pdg_result_server = os.environ.get('PDG_RESULT_SERVER', None)
    if pdg_result_server:
        server_check_period = 120
        host, port = pdg_result_server.split(':')
        ping_pdg = PDGPingHelper(host, int(port), server_check_period)
        while True:
            if simtracker.waitForCompletion(server_check_period):
                break
            try:
                ping_pdg.ensureReachable()
            except Exception as e:
                printlog(str(e))
                break
    else:
        simtracker.waitForCompletion()

def stopTracker(tracker_host, tracker_port):
    # Connect to the tracker.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((tracker_host, tracker_port))

    # Send the quit message.
    msg = b"quit"
    msg_len = struct.pack("!L", len(msg))
    msg = msg_len + msg
    s.send(msg)

    # Read the ack from tracker and send back an empty message to indicate
    # success.
    s.recv(1)
    s.send(b'')

    s.close()

if __name__ == "__main__":
    usage = 'usage: %prog [options] trackerport webport'
    parser = optparse.OptionParser(usage=usage)
    parser.add_option("-v", "--verbose", dest="verbose",
                      help="Turn on verbose output.", default=False,
                      action="store_true")
    parser.add_option("-s", "--stop", dest="stop",
                      help="Stops an existing tracker.", default=False,
                      action="store_true")
    options, args = parser.parse_args()

    if options.stop:
        work_item = WorkItem.fromJobEnvironment(expand_strings=False)
        host = work_item.stringAttribValue('trackerhost')
        port = work_item.intAttribValue('trackerport')

        stopTracker(host, port);
    else:
        if len(args) < 2:
            parser.error('Both tracker and web port must be specified.')

        port = int(args[0])
        webport = int(args[1])

        initTracker(port, webport, options.verbose)
