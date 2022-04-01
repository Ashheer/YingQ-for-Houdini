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
# NAME:	        sharedserver.py ( Python )
#
# COMMENTS:     start a long-running shared server.  Wait for the
#               server to be ready and then report the server info such as pid back to PDG callback server.
#
#               Results are the data sent back from the rpc server, as a simple dict:
#               {'status' : 'success', 'result': 'myresult'}
#
#               Note: This module is intended to not have any dependency on a Houdini installation.
#               ## xmlproc protocol ##
#               shutdown()
#               exec_scriptfile(file_path)
#
#               ## raw protocol ##
#               Message is sent with message length prepended.  Server sends ACK char and client
#               sends empty message to indicate pipe close.  'quit' is understood to mean server should
#               shutdown.
#
#               Example:
#               msg = "quit"
#               msg_len = struct.pack("!L", len(msg))
#               msg = msg_len + msg
#               s.send(msg)
#               # Read the ack from server and send back an empty message to indicate success
#               s.recv(1)
#               s.send('')

from distutils.spawn import find_executable
import subprocess
import os
import sys
import logging
import socket
import struct
import time
import traceback
import re
import platform
try:
    from pdgcmd import reportServerStarted
except ImportError:
    from pdgjob.pdgcmd import reportServerStarted
if sys.version_info.major >= 3:
    import xmlrpc.client as xmlrpclib
else:
    import xmlrpclib

logger = logging.getLogger(__name__)

def startSharedServer(*argv):
    """
    Start a server using argv
    """
    sanitizeEnvVars()

    if (not os.path.isfile(argv[0])) and (find_executable(argv[0]) is None):
        if os.name != 'nt' or argv[0].endswith(('.bat', '.cmd', '.com')):
            err = "Could not find executable '{}'".format(argv[0])
            raise RuntimeError(err)
    logger.info("Starting sharedserver : %s", argv)

    creationflags = 0
    startupinfo = None
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        if int(os.environ.get('PDG_BREAKAWAY_FROM_JOB', 0)) > 0:
            # These are required to detach the process from the parent Job on Windows,
            # which we generally want to do because the server is actually a long-
            # running side-effect of this task, we don't want it to end when the task ends.
            #
            # Python 3 adds the following 2 flags as part of subprocess, but for 2.7
            # we need to define them
            DETACHED_PROCESS = 0x00000008
            CREATE_BREAKAWAY_FROM_JOB = 0x01000000
            creationflags = DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB
    
    proc = subprocess.Popen(argv,
                            shell=False,
                            stdin=subprocess.PIPE,
                            startupinfo=startupinfo,
                            creationflags=creationflags)
    proc.stdin.close()
    return proc

def sanitizeEnvVars():
    """Clear out Houdini-related paths from PATH and LD_LIBRARY_PATH."""
    hfs = os.environ.get("HFS")
    if not hfs:
        return

    if 'PYTHONHOME' in os.environ and hfs in os.environ['PYTHONHOME']:
        del os.environ['PYTHONHOME']

    #Only clear these out on non-Windows platforms.
    if sys.platform.startswith("win"):
        return

    TO_SANITIZE = ("LD_LIBRARY_PATH", "PATH")

    for var_name in TO_SANITIZE:
        paths = os.environ.get(var_name, "").split(":")
        sanitized_paths = [
            path for path in paths if hfs not in path
        ]
        sanitized_value = ":".join(sanitized_paths)
        os.environ[var_name] = sanitized_value

def _sendraw(msg, hostname, port, timeout, recv_ack_only=True):
    """
    Send a message using raw protocol.
    """
    data = None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((hostname, port))
    # Send the message.
    if sys.version_info.major >= 3:
        msg = bytes(msg, 'utf-8')
    msg_len = struct.pack("!L", len(msg))
    msg = msg_len + msg
    s.send(msg)
    if recv_ack_only:
        # receive ACK
        s.recv(1)
        # send pipe-close
        s.send(b'')
    else:
        lengthdata = ''
        while len(lengthdata) < 4:
            chunk = s.recv(4 - len(lengthdata))
            if chunk == b'':
                logger.error('Error in reading length of message')
                raise RuntimeError("Socket broken")
            lengthdata = lengthdata + chunk
        (length,) = struct.unpack('!L', lengthdata)
        logger.info("Read a message length %s", length)

        # Sanity test the length.
        if length >= 16777216:
            return ''

        data = ''
        while len(data) < length:
            chunk = s.recv(length - len(data))
            if chunk == '':
                logger.error('Error reading buffer, bytes: ' + str(len(data)) +
                             ' of ' + str(length) + ' body is ' + data)
                raise RuntimeError("Socket broken")
            data = data + chunk

        # Send an ack.
        s.send(b'j')

        # This should be '' to mark the pipe
        # closing.
        _ = s.recv(1)

    s.close()
    return data

def waitOnPort(hostname, port, proto_type, timeout, proc, log_fname):
    addr = (hostname, port)
    logger.info("Attempting to connect to sharedserver at %s", addr)
    started_at = time.time()
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.connect(addr)
            if proto_type == 'raw':
                # do proper disconnect
                msg = struct.pack("!L", 0)
                s.send(msg)
                s.recv(1)
                s.send(b'')
                s.close()
            # with xmlrpc we can just pull the plug
            break
        except socket.error:
            err = proc.poll()

            if err is not None and err != 0:
                # If the process errored out, wait a moment to see if a log file
                # was written, and display it if possible.
                for _ in range(0, 10):
                    try:
                        with open(log_fname, 'r') as fp:
                            raise RuntimeError('Log file contents:\n\n{}'.format(fp.read()))
                    except IOError:
                        time.sleep(0.05)

                raise RuntimeError("Failed to execute work item. Log file could not be read.")

            if time.time() - started_at > timeout:
                raise RuntimeError("Failed to connect to sharedserver at {} in {} seconds".format(addr, timeout))
            time.sleep(0.5)

    return True

def findFreePort(hostname, port):
    """
    Find a port that's not taken
    Note: when port == 0, we are asking the system to grab
    a random free port.  Otherwise we are searching for one
    from the given range
    """
    for n in range(port, 65535):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_addr = (hostname, n)
            s.bind(test_addr)
            # it worked, lets go with that
            found_port = s.getsockname()[1]
            s.close()
            return found_port
        except socket.error as e:
            if e.errno == 98 or e.errno == 10048:
                #logger.debug("Port {} is already in use".format(n))
                continue
            else:
                raise
    raise RuntimeError("Could not find a free TCP in range {}-65535".format(port))

def shutdownServer(info, timeout=30):
    """
    Shutdown the remote server.
    """
    host = info['host']
    port = info['port']
    proto_type = info.get('proto_type', 'xmlrpc')
    try_count = 3
    s = xmlrpclib.ServerProxy('http://{}:{}'.format(host, port))
    while try_count > 0:
        try:
            socket.setdefaulttimeout(timeout)
            if proto_type == 'raw':
                _ = _sendraw('quit', host, port, timeout)
            else:
                _ = s.shutdown()
            logger.info('quit was acknowledged')
            return True
        except socket.timeout:
            return False
        except socket.error as e:
            if e.errno == 10061:
                if try_count > 1:
                    time.sleep(0.1)
                    try_count -= 1
                else:
                    return False
        except xmlrpclib.Fault as err:
            print("Failed shutdown with Fault {} '{}'".format(
                err.faultCode, err.faultString))
        finally:
            socket.setdefaulttimeout(None)
    return True

def pingServer(hostname, server_port, socktimeout):
    """
    Raises an exception if the server cannot be reached.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(socktimeout)
    s.connect((hostname, server_port))
    s.close()

def getHostname():
    """
    Returns a hostname to be used by other hosts on the farm.
    """
    try:
        net_hostname = socket.getfqdn()
        socket.gethostbyname(net_hostname)
        assert(net_hostname != 'localhost')
    except:
        try:
            net_hostname = platform.node()
            socket.gethostbyname(net_hostname)
        except:
            net_hostname = socket.gethostname()
    return net_hostname

def isAddressLoopback(host_address):
    """
    Return True if address is a loopback
    """
    if host_address == 'localhost':
        return True
    if sys.version_info.major >= 3:
        import socket
        import ipaddress
        host_address = socket.gethostbyname(host_address)
        return ipaddress.ip_address(host_address).is_loopback
    # FIXME: check if address in 127.0.0.0/8
    return host_address == '127.0.0.1'

def waitOnConnectionFile(connectionfile_local, timeout):
    """
    Wait for port number to be written to the given file within
    the given number of seconds timeout.  Return the port number or None
    """
    port = None
    start_t = time.time()
    while time.time() - start_t < timeout:
        time.sleep(0.66)
        if os.path.isfile(connectionfile_local):
            try:
                output = open(connectionfile_local, 'r+').read()
                os.remove(connectionfile_local)
                m = re.search(r'RPC Port:(\d+)', output)
                port = int(m.group(1))
            except IOError:
                pass
        if port:
            break
    return port

def main():
    import argparse

    parser = argparse.ArgumentParser(description="""
Starts a shared server which exposes an XMLRPC or text-based TCP command API.
When starting up, the given command substitutes {host} and {port} with
the determined values and {log} with a path that server should log output to.
""")
    parser.add_argument('--port', default=0, type=int,
                        help="The TCP port to bind to.  0 means choose random")
    parser.add_argument('--host', default='',
                        help="The IPV4 address the server is bound to")
    parser.add_argument('--timeout', default=15, type=float,
                        help="The timeout in seconds for waiting on server to start.")
    parser.add_argument('--socktimeout', type=float, default=3E4,
                        help="The timeout in seconds for socket send and recv, 0 means "
                             "non blocking.")
    parser.add_argument('--name', default='', required=False,
                        help="The name of the sharedserver, used with sharedserver"
                             "scheduler API")
    parser.add_argument('--proto_type', default='xmlrpc', required=False,
                        help="server protocol: \"raw\" means use raw tcp messages \"quit\" "
                             "to quit\n\"xmlrpc\" means using xmlrpc")
    parser.add_argument('--tag',
                        help="[DEPRECATED] Data Tag to use for the command result")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--start', action='store_true',
                       help="Start a server using the positional arguments. 'sharedserver' "
                            "info is reported back to PDG")
    group.add_argument('--ping', action='store_true',
                       help="Check that server is running, fails if server cannot be contacted")  
    group.add_argument('--quit', action='store_true',
                       help="Sends the quit command to the server")

    parser.add_argument('servercommand', nargs=argparse.REMAINDER,
                        help='The server command - for example: $PDG_MAYAPY mayarpc.py" [REQUIRED]')
    args = parser.parse_args()

    if args.start and (not args.servercommand):
        logger.error('Server command must be specified.  See --help.')
        exit(1)

    if args.tag:
        logger.warning("--tag argument is deprecated")

    server_host = args.host
    server_port = args.port
    servercmd_fmt = args.servercommand

    if args.start:
        try:
            item_name = os.environ['PDG_ITEM_NAME']
            callbackserver = os.environ['PDG_RESULT_SERVER']
        except KeyError as ex:
            logger.error("%s must be in environment or specified via argument flag", ex)
            exit(1)
        callback_hostname = callbackserver.rsplit(':', 1)[0]
        logger.info("callback_hostname: {}".format(callback_hostname))
        is_local_cook = isAddressLoopback(callback_hostname)
        if not server_host:
            # We should default to using loopback if we are doing a local-machine
            # cook.  This can be determined by looking at the PDG_RESULT_SERVER. If
            # it's loopback, then we must be a local cook.
            if is_local_cook:
                server_host = callback_hostname
            else:
                # Result server is not on loopback, so default to
                # binding to all interfaces
                server_host = '0.0.0.0'
        # net_hostname is the address that can be found by other farm machines
        if is_local_cook:
            net_hostname = 'localhost'
        else:
            net_hostname = getHostname()
        
        tempdir = os.environ['PDG_TEMP']
        log_fname = '{}/logs/{}_server.log'.format(tempdir, item_name)
        log_fname = os.path.expandvars(log_fname)
        connectionfile_local = '{}/{}_connection.txt'.format(tempdir, item_name)

        # if they have --connectionfile, use that instead of finding one here
        use_connectionfile = False
        for tok in servercmd_fmt:
            if '{connectionfile}' in tok:
                use_connectionfile = True

        if not use_connectionfile:
            port = findFreePort(server_host, server_port)
        else:
            if os.path.isfile(connectionfile_local):
                os.remove(connectionfile_local)
            port = 0
        
        servercmd = [tok.format(host=server_host, log=log_fname,
            port=port, connectionfile=connectionfile_local) for tok in servercmd_fmt]

        proc = startSharedServer(*servercmd)

        if not use_connectionfile:
            waitOnPort(callback_hostname, port, args.proto_type, args.timeout, proc, log_fname)
        else:
            port = waitOnConnectionFile(connectionfile_local, args.timeout)      
            if not port:
                logger.error("Could not determine port from connection file {}".format( 
                    connectionfile_local))
                exit(1)
        reportServerStarted(args.name, proc.pid, callback_hostname, port,
            args.proto_type, log_fname)
    elif args.ping:
        logger.info("Sending Ping")
        pingServer(server_host, server_port, args.socktimeout)
    elif args.quit:
        logger.info("Sending Quit")
        info = {
            'host' : server_host,
            'port' : server_port,
            'proto_type' : args.proto_type
        }
        shutdownServer(info, args.socktimeout)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except Exception as e:
        logger.error(traceback.format_exc())
        sys.exit(1)
