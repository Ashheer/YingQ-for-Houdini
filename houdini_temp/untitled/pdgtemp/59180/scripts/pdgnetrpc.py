from __future__ import print_function
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
# NAME:         pdgnetrpc.py ( Python )
#
# COMMENTS:     PDGNet RPC utility for invoking remote methods over the network
#               using PDGNet MQ server, MQ relay client.
#
#               
#

import os
import sys
import pickle
import time
import threading
from random import uniform
import socket
import subprocess
import traceback

is_py2 = sys.version_info.major < 3
# For HTTP
if is_py2:
    from urllib2 import urlopen, Request, URLError, HTTPError
else:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError

if is_py2:
    from Queue import Queue
    from Queue import Empty as Queue_Empty
else:
    from queue import Queue
    from queue import Empty as Queue_Empty

try:
    from pdgjob.pdgnetjson import PDGNetRPCJsonEncoder, PDGNetRPCJsonDecoder, PDGNetRPCJsonWrapper
except:
    from pdgnetjson import PDGNetRPCJsonEncoder, PDGNetRPCJsonDecoder, PDGNetRPCJsonWrapper

LogDefault, LogVerbose = 0, 1
def printlog(msg, msg_verbosity=LogDefault):
    # Do not use this from module-level because there is potential circular import
    try:
        import pdgcmd
        pdgcmd.printlog(msg, msg_verbosity)
    except ImportError:
        print(msg)

def rpcException():
    # Do not use this from module-level because there is potential circular import
    try:
        from pdgcmd import RpcError
        return RpcError
    except ImportError:
        return RuntimeError

def throwRpcException(msg):
    # Do not use this from module-level because there is potential circular import
    raise rpcException()(msg)

# This brings in pdgnet RPC mechanism, but
# we'll fallback to using HTTP RPC if not
# available (i.e. no Houdini install)
use_http = False
httpport = 0
try:
    from pdgutils import mqSendMessageGetReplyAsync
except:
    # Fallback to RPC over HTTP since no Houdini available
    use_http = True

    # TODO: Get HTTP port from env for now but change it in future?
    httpport = os.environ.get('PDG_HTTP_PORT', 0)

# RPC Constants
PDGNET_RPC_NOTIMEOUT = -1
    
# HTTP Result & Errors
PDGNET_HTTP_SUCCESS = 200
PDGNET_HTTP_GENERAL_ERROR = 99999

# Internal reponse parsing
PICKLE_START_TOKEN=b'PDGNET_START_RESPONSE'

def doHTTPRequest(url, headers, content, timeout_ms):
    """
    Send a HTTP request and return reply.
    Returns (status code, text) tuple.
    """
    try:
        if timeout_ms > 0:
            tmsec = int(timeout_ms / 1000.0)
            socket.setdefaulttimeout(tmsec)

        # Encoding to bytes for python3 urlopen
        data = content.encode('utf-8')
        req = Request(url, data=data, headers=headers)
        res = urlopen(req)
        code = res.getcode()
        msg = res.read()
        #printlog("http res: code={}, msg={}".format(code, msg))
    except (URLError, HTTPError) as ue:
        if ue.errno == None:
            ue.errno = PDGNET_HTTP_GENERAL_ERROR
        code = ue.errno
        msg = ue.reason
    except Exception as e:
        # General error
        code = PDGNET_HTTP_GENERAL_ERROR
        msg = str(e)
    return (code, msg)

class PDGNetRPCHTTPMessage(object):
    def __init__(self):
        self.response_queue = None
        self.process = None
        self.complete = False
        self.response_code = 0
        self.response_msg = None

    def doRequest(self, url, port, get_reply, reply_id, client_name, content, timeout_ms):
        if self.process:
            raise throwRpcException("Invalid RPC HTTP message state!")

        self.complete = False
        self.response_code = 0
        self.response_msg = None

        full_url = "http://{}:{}/result".format(url, port)
        # type=3 is PDGNetMessageType.PDGN_MSG_PUSH
        headers = {'type': 3, 'reply':int(get_reply), 'replyid':0, 'clientid':client_name}

        # We do the http request in a seperate process by invoking python on
        # this script file, the arguments send by stdin and result from stdout.
        # This workaround is because stock python2/urllib2 does not support async http
        # requests which we need for this .
        self.response_queue = Queue()
        args = (full_url, headers, content, timeout_ms)
        
        # FIXME: Enable to do request directly, why is this necessary
        #self.response_queue.put(doHTTPRequest(*args))
        #return

        myscript = os.path.abspath(__file__)
        self.process = subprocess.Popen([sys.executable, myscript],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        
        
        # read from http process stdout in a worker thread
        def _enqueue_output():
            pickle_args = pickle.dumps(args, pickle.HIGHEST_PROTOCOL)
            stdoutd, stderrd = self.process.communicate(pickle_args)
            ix = stdoutd.find(PICKLE_START_TOKEN)
            stdoutd = stdoutd[ix + len(PICKLE_START_TOKEN):]
            try:
                resp = pickle.loads(stdoutd)
            except:
                traceback.print_exc()
                resp = (PDGNET_HTTP_GENERAL_ERROR, 'No Response')
            if stderrd:
                print(stderrd, file=sys.stderr)
            self.response_queue.put(resp)
        
        t = threading.Thread(target=_enqueue_output)
        t.daemon = True
        t.start()

    def getError(self):
        return self.response_code

    def getErrorMessage(self, error_code):
        return self.response_msg

    def hasReplyAsyncReceived(self):
        if not self.complete:
            try:
                self.response_code, self.response_msg = self.response_queue.get_nowait()
                # 200 is success, but convert to 0 to match PDGNetMessage success code
                if self.response_code == PDGNET_HTTP_SUCCESS:
                    self.response_code = 0
                else:
                    if not self.response_msg:
                        self.response_msg = "Unknown error"
                self.complete = True
            except Queue_Empty:
                # No result yet, keep waiting
                pass
            except Exception as e:
                # Something else failed, so assume we're done
                self.response_code = PDGNET_HTTP_GENERAL_ERROR
                self.response_msg = str(e)
                self.complete = True
        return self.complete

    def completeReplyAsync(self):
        if self.process:
            self.process.wait()
            self.process = None
        self.response_queue = None
        return self.response_code

    def readString(self):
        return self.response_msg


def sendMessageGetReplyAsync(url, port, client_name, json_str, timeout_ms):
    if use_http:
        # For HTTP, use PDGNetRPCHTTPMessage to mimic PDGNMessage below

        if httpport == 0:
            raise throwRpcException('"PDGNetRPC requires PDG_HTTP_PORT in envornment')

        get_reply = True
        reply_id = 0
        request = PDGNetRPCHTTPMessage()
        request.doRequest(url, httpport, get_reply, reply_id, client_name, json_str, timeout_ms)
        return request
    else:
        # Returns PDGNMessage
        return mqSendMessageGetReplyAsync(url, port, client_name, json_str, timeout_ms)

class PDGNetRPCMessage(object):
    """ Single RPC call message handler for PDGnet """

    def __init__(self, server_url, server_port, client_name, get_reply, func_name, timeout_ms, jsonwrapper=PDGNetRPCJsonWrapper):
        self.server_url = server_url
        self.server_port = server_port
        self.client_name = client_name
        self.get_reply = get_reply
        self.func_name = func_name
        self.timeout_ms = timeout_ms
        self.jsonwrapper = jsonwrapper()
        self.lasterror = 0

    def __call__(self, *args):
        json_str = self.jsonwrapper.rpcToString(self.func_name, *args)
        return self.doRPC(json_str)

    def doRPC(self, json_str):
        #printlog("RPC call: server={}:{}, clientid={}, wait={}, json={}".format( 
        #    self.server_url, self.server_port, self.client_name, self.get_reply, json_str))

        # RPC with reply: send the message, get reply asynchronously so that we don't block the GIL
        reply_msg = sendMessageGetReplyAsync(self.server_url, 
            self.server_port, self.client_name, json_str, self.timeout_ms)

        self.lasterror = reply_msg.getError()
        if self.lasterror != 0:
            raise throwRpcException("PDGnet RPC send-get-reply failed. (error {}: {})".format(
                self.lasterror, reply_msg.getErrorMessage(self.lasterror)))
        else:
            # Wait until reply is received or timed out
            start_time = time.time()
            timeout_s = self.timeout_ms / 1000
            while not reply_msg.hasReplyAsyncReceived():
                if self.timeout_ms != PDGNET_RPC_NOTIMEOUT:
                    if (time.time() - start_time) > timeout_s:
                        raise throwRpcException('Timed Out ({}s)'.format(timeout_s))
                time.sleep(0.0001)

            self.lasterror = reply_msg.completeReplyAsync()
            if self.lasterror != 0:
                raise throwRpcException("PDGnet RPC get-reply failed (error {}: {})".format(
                    self.lasterror, reply_msg.getErrorMessage(self.lasterror)))
            elif self.get_reply:
                # TODO: reply_msg.readString() should throw exception if fails to read
                reply_json = self.jsonwrapper.stringToJson(reply_msg.readString())
                return reply_json["reply"]
            else:
                # Can ignore reply message if caller doesn't want reply (just an ACK)
                pass

        return None

# PDGNet Rpc Delegate implementation for pdgcmd.  This module is imported by
# pdgcmd and is used as an Rpc Proxy factory.

# Maximum number of retries for callbacks that get ECONNREFUSED
# By default we don't retry, because we assume it indicates cancellation of 
# the cook where the MQ has been shut down before we were able to RPC back.
max_retries = int(os.environ.get('PDG_RPC_RETRIES', 1))
# seconds of timeout on making socket connections for RPC
rpc_timeout = int(os.environ.get('PDG_RPC_TIMEOUT', 5))
# maximum seconds to wait between retries after a timeout
rpc_backoff = int(os.environ.get('PDG_RPC_MAX_BACKOFF', 4))

class PDGNetRPC(object):
    """ Single RPC message creator for PDGnet """

    def __init__(self, server_url, server_port, client_name, wait_reply,
                 timeout_ms=None, rpcmsgclass=PDGNetRPCMessage):
        if not server_url or not client_name or server_port == 0:
            raise throwRpcException("MQ address not valid (host={}, port={}, "
                               "client={}".format(server_url, server_port, client_name))

        self.server_url = server_url
        self.server_port = server_port
        self.client_name = client_name
        self.wait_reply = wait_reply
        self.timeout_ms = timeout_ms
        self.rpcmsgclass = rpcmsgclass


    def __getattr__(self, func_name):
        """
        Returns new rpcmsgclass object that should allow to invoke RPC over network.
        """
        # defer calcuation of timeout so that it can be changed on the fly
        if self.timeout_ms is None:
            timeout_ms = int(rpc_timeout * 1000)
        else:
            timeout_ms = int(self.timeout_ms)
        return self.rpcmsgclass(self.server_url, self.server_port,
            self.client_name, self.wait_reply, func_name, timeout_ms)


class PDGNetRPCMulti(object):
    """ Multi RPC message creator for PDGnet """

    def __init__(self, server_url, server_port, client_name,
                 timeout_ms=None,
                 jsonwrapper=PDGNetRPCJsonWrapper, rpcmsgclass=PDGNetRPCMessage):
        if not server_url or not client_name or server_port == 0:
            raise throwRpcException("MQ address not valid (host={}, port={}, "
                               "client={}".format(server_url, server_port, client_name))

        self.server_url = server_url
        self.server_port = server_port
        self.client_name = client_name
        self.timeout_ms = timeout_ms
        self.jsonwrapper = jsonwrapper()
        self.jsonmethods = []
        self.rpcmsgclass = rpcmsgclass

    def __call__(self):
        json_str = self.jsonwrapper.dumps(self.jsonmethods)

        # defer calcuation of timeout so that it can be changed on the fly
        if self.timeout_ms is None:
            timeout_ms = int(rpc_timeout * 1000)
        else:
            timeout_ms = int(self.timeout_ms)

        rpcmsg = self.rpcmsgclass(self.server_url, self.server_port,
            self.client_name, True, None, timeout_ms)
        rpcmsg.doRPC(json_str)
        # Ignore reply which is just an ACK
            
    def __getattr__(self, func_name):
        """
        Returns a function object that tracks RPC calls. When the object itself is
        called via (), the message is sent and the RPCs are invoked.
        """
        def addmethod(*args):
            self.jsonmethods.append(self.jsonwrapper.rpcToDict(func_name, *args))
            return None
        return addmethod

class PDGNetRpcProxy:
    # RPC Proxy object for PDGNet connection
    def __init__(self, host, port, client_id, get_reply, multi, raise_exc=True):
        if multi:
            self._s = PDGNetRPCMulti(host, port, client_id)
        else:
            self._s = PDGNetRPC(host, port, client_id, get_reply)

        self._raise_exc = raise_exc

    def call(self, fn_name, *args, **kwargs):
        # perform the RPC.  In the case of multi-call it will just register the
        # call which gets later sent by commitMulti, by invoking __call__ on the
        # PDGNetRPCMulti
        fn_attr = getattr(self._s, fn_name)
        return self._invokePDGNetRpcFn(fn_attr, fn_name, *args)

    def commitMulti(self, name):
        self._invokePDGNetRpcFn(self._s, name)

    def _invokePDGNetRpcFn(self, fn, fn_name, *args):
        # Invoke the PDGNetRPCMessage with the passed arguments, this is a
        # blocking network call
        try_count = max_retries + 1
        max_sleep = rpc_backoff
        while try_count > 0:
            try:
                return fn(*args)
            except rpcException() as err:
                try_number = max_retries + 2 - try_count
                if try_count > 1:
                    backoff = uniform(max_sleep/(try_count-1), max_sleep/try_count)
                    printlog(
                        "Connection refused. Retry with back off: {:.1f}s {}/{}".format(
                            backoff, try_number, max_retries))
                    time.sleep(backoff)
                    continue
                traceback.print_exc()
                if try_count > 1:
                    printlog("Failed RPC {} with error: {}. Retry {}/{}".format(
                        fn_name, err, try_number, max_retries))
                else:
                    printlog('Failed RPC {} with error: {}'.format(
                        fn_name, err))
            finally:
                try_count -= 1
        msg = 'Failed RPC: {} {}'.format(fn_name, args)
        printlog(msg)

        if self._raise_exc:
            raise throwRpcException(msg)

def createRpcProxy(host, port, client_id, get_reply, multi, raise_exc):
    # Create the delegate object
    proxy = PDGNetRpcProxy(host, port, client_id, get_reply, multi, raise_exc)
    return proxy

if __name__ == '__main__':
    # this script is run to make an http return the result in stdout
    if is_py2:
        fout = sys.stdout
    else:
        fout = sys.stdout.buffer
    def pickledump(obj):
        pickle.dump(obj, fout, pickle.HIGHEST_PROTOCOL)
    if is_py2:
        args = pickle.loads(sys.stdin.read())
    else:
        args = pickle.load(sys.stdin.buffer, encoding='utf8', errors='replace')
    try:
        res = doHTTPRequest(*args)
        if res:
            # print where the response begins in case there is other stuff being
            # printed to stdout
            fout.write(PICKLE_START_TOKEN)
            pickledump(res)
    except Exception as e:
        pickledump((PDGNET_HTTP_GENERAL_ERROR, str(e)))
