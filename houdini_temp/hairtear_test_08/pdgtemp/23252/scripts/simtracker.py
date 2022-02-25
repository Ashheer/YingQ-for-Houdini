# simtracker.py
#
# This standalone script is designed to be excecuted as:
#       python simtracker.py 8000 9000
# where 8000 is the port number for the tracker and 9000 for the webserver.
# These ports can be changed, or 0 specified to auto-assign a port.
# Special Houdini OPs, such as ROP_NetBarrier and DOP_NetFetch can point
# at the tracker for syncrhonization and peer discovery.
#
# The tracker should be on a separate machine from any of the live machines
# or the peers might be reported as "127.0.0.1" which causes a few problems.
# Often removing the explicit loopback address from /etc/hosts will avoid
# this, allowing the tracker to be on a live machine.
#
# While the tracker can theoritcally serve multiple sims, this is not
# at all recomended as it will be a single point of failure.  It is
# expected to create a new tracker for every sim.  Specifying 0
# for the port will select random free ports, which will be output if
# -v is specified.
#
# Connect your browser to http://trackeraddress:9000/ to get information
# about the tracker state.  This is useful to debug and see if machines
# are checking in and are synchronized.
#
# Note that distribution is peer-to-peer.  The tracker isn't a command
# and control node, but more a peer-discovery service.   The usual
# way to setup a distributed sim in a queue is to use three types
# of jobs:
# 1) The tracker job.  This is a zero-cpu job.  Starts the tracker
#    and determines port addresses.  Waits for the tracker to terminate.
# 2) The actual slice jobs.  These are hython invocations for each
#    slice of the sim.  They are started after #1 has determined
#    the port numbers and provided the tracker port.
# 3) The terminate tracker job.  This job is made contingent on
#    the #2 jobs completing.   When they complete, it runs and sends
#    a quit message to the tracker.  This allows the #1 to terminate.
# The extra termination is required as the tracker doesn't know
# how big the sim is so can't tell when the slices are complete.
#
# To terminate the tracker, connect to its tracker port and send
# a quit message:
#
#   # Connect to the tracker.
#   s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#   s.connect((tracker_host, tracker_port))
#
#   # Send the quit message.
#   msg = "quit"
#   msg_len = struct.pack("!L", len(msg))
#   msg = msg_len + msg
#   s.send(msg)
#
#   # Read the ack from tracker and send back an empty message to indicate
#   # success.
#   s.recv(1)
#   s.send('')
#
#   s.close()

from __future__ import print_function

import socket
import sys
import struct
import time
if sys.version_info.major >= 3:
    import queue
    from http.server import *
else:
    import Queue as queue
    from BaseHTTPServer import *

import threading
import copy
import cgi

# Active threads:
# HttpServerThread - displays status info
# ListenThread - accepts() connections and puts them on the glb_readqueue
# ReadThread - reads connections on the glb_readqueue and puts (addr, msg)
#               the glb_msgqueue
# WriteThread - reads (peer, messages) on the glb_writequeue and
#               sends the message.

glb_readqueue = queue.Queue(0)
glb_writequeue = queue.Queue(0)
glb_msgqueue = queue.Queue(0)

# Stores a list of all jobs.  Each job is indexed by its job
# name and consists of a dictionary
#   nproc - number of procs in this job.
#   done - number of procs that have completed
#   error - Job has entered an error state.
#   starttime - time of first acquire
#   synctime - when all peers acquired
#   donetime - when last done was received
#   proclist - list of connected procs
#     proc - proc number
#     port - port
#     acquiretime - when this peer connected
#     donetime - when this peer was done
#     address - address

joblist = {}
glb_donelist = []
glb_doneEvent = threading.Event()
glb_doneEvent.clear()

glb_joblock = threading.RLock()

# Stores a list of all barriers.
#   val - current barrier value
#   settime - time of last set
#   proclist - list of waiting procs
#     jobname - our own job name
#     waitval - when val >= to this, can awake the proc.
#     port - port
#     address - address
#     acquiretime - when this wait was enabled
#     dontime - when this condition was met

glb_barrierlist = {}
glb_barrierdone = []

# This event is used to synchronize objects waiting for the tracker 
# to be in a state ready to accept incoming connections.
glb_listenReadyEvent = threading.Event()
glb_listenReadyEvent.clear()

# This event is used to synchronize objects waiting for
# the web server to start up.
glb_webReadyEvent = threading.Event()
glb_webReadyEvent.clear()

# Choose the best timer for our platform.
if hasattr(time, "perf_counter"):
    default_timer = time.perf_counter
elif sys.platform == "win32":
    default_timer = time.clock
else:
    default_timer = time.time

glb_port = 0
def getListenPort():
    global glb_port, glb_listenReadyEvent
    glb_listenReadyEvent.wait()
    return glb_port

glb_webPort = 0
def getWebPort():
    global glb_webPort, glb_webReadyEvent
    glb_webReadyEvent.wait()
    return glb_webPort

glb_verbose = False 
def printMsg(msg):
    global glb_verbose
    if not glb_verbose:
        return

    try:
        print(msg)
        sys.stdout.flush()
    except:
        # An exception could be thrown here because
        # the main thread might have quit already and closed
        # stdout and stderr.
        pass

def setVerbosity(verbose):
    global glb_verbose
    glb_verbose = verbose

def waitForCompletion(timeout=None):
    # Returns True on completion, or False on timeout when the
    # timeout argument is not None
    global glb_doneEvent
    return glb_doneEvent.wait(timeout)

def waitForListener():
    global glb_listenReadyEvent
    glb_listenReadyEvent.wait()

def writeBarrierInfo(wfile, job, jobname):
    wfile.write('<b>Barrier: ' + jobname + '</b>')
    wfile.write(': @%f, val %d' % (job['settime'], job['val']))
    wfile.write('<br>')

    writeBarrierPeerInfo(wfile, job['proclist'])

def writeBarrierPeerInfo(wfile, peerlist):
    wfile.write('<table border="1" width="100%"><tr><td>Job Name</td><td>Peer Info</td><td>Wait Value</td><td>Waiting Time</td></tr>')

    for peer in reversed(peerlist):
        wfile.write('<tr>')
        wfile.write('<td>%s</td>' % (peer['jobname'],))
        wfile.write('<td>peer (@%f) - %s : %d</td>' %
            (peer['acquiretime'], peer['address'], peer['port']))
        if peer['donetime'] < 0:
            wfile.write('<td>%d</td><td>%fs</td>' %
                (peer['waitval'], default_timer() - peer['acquiretime']))
        else:
            wfile.write('<td>%d</td><td>%fs</td>' %
                (peer['waitval'], peer['donetime'] - peer['acquiretime']))
        wfile.write('</tr>')

    wfile.write('</table><br>')


def writeJobInfo(wfile, job, jobname):
    wfile.write('<b>Job: ' + jobname + '</b>')
    wfile.write(': @%f, (n: %d, a: %d, d: %d, e: %d)' %
            (job['starttime'], job['nproc'], len(job['proclist']), job['done'], job['error']))
    if job['synctime'] >= 0:
        wfile.write('<br>acquire->sync: %fs' % (job['synctime'] - job['starttime'],))
    if job['donetime'] >= 0:
        wfile.write('<br>sync->done: %fs' % (job['donetime'] - job['synctime'],))
    wfile.write('<br>')

    wfile.write('<table border="1" width="100%"><tr><td>Peer Info</td><td>acquire->sync</td><td>acquire->done</td><td>sync->done</td></tr>')

    for peer in job['proclist']:
        wfile.write('<tr>')
        wfile.write('<td>peer #%d (@%f) - %s : %d</td>' %
            (peer['proc'], peer['acquiretime'], peer['address'], peer['port']))
        if job['synctime'] >= 0:
            wfile.write('<td>%fs</td>' % (job['synctime'] - peer['acquiretime'],))
        else:
            wfile.write('<td>pending</td>')
        if peer['donetime'] >= 0:
            wfile.write('<td>%f</td>' % (peer['donetime'] - peer['acquiretime'],))
            if job['synctime'] >= 0:
                wfile.write('<td>%f</td>' % (peer['donetime'] - job['synctime'],))
            else:
                wfile.write('<td>pending</td>')
        else:
            wfile.write('<td>pending</td><td>pending</td>')
        wfile.write('</tr>')

    wfile.write('</table><br>')

def writeHtml(wfile):
    global joblist, glb_donelist, glb_joblock

    wfile.write('<html><head>')
    wfile.write('<title>Tracker Status</title>')
    wfile.write('<meta http-equiv=refresh content="30">')
    wfile.write('</head>')
    wfile.write('<body>')

    with glb_joblock:
        activejobs = copy.deepcopy(joblist)
        donelist = copy.deepcopy(glb_donelist)

    wfile.write('<h3>Active List</h3>')

    for jobname, job in list(activejobs.items()):
        writeJobInfo(wfile, job, jobname)
        wfile.write('<br>')

    wfile.write('<hr><h3>Barriers</h3>')

    for jobname, job in list(glb_barrierlist.items()):
        writeBarrierInfo(wfile, job, jobname)
        wfile.write('<br>')

    wfile.write('<hr><h3>Done List</h3>')
    for job in reversed(donelist):
        writeJobInfo(wfile, job, job['jobname'])
        wfile.write('<br>')

    wfile.write('<hr><h3>Done Barriers</h3>')
    writeBarrierPeerInfo(wfile, glb_barrierdone)

    wfile.write('</body></html>')

class TrackerHttpServer(HTTPServer):
    def __init__(self, address):
        HTTPServer.__init__(self, address, TrackerHttpHandler)

class WFileAdaptor:
    # send data as bytes
    def __init__(self, wfile):
        self.wfile = wfile
    def write(self, s):
        self.wfile.write(bytes(s, 'utf-8'))

class TrackerHttpHandler(BaseHTTPRequestHandler):
    def __init__(self, request, client_address, server):
        BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    def do_GET(self):
        printMsg('Got a request from ' + self.path)

        self.send_response(200)
        self.end_headers()
        if sys.version_info.major >= 3:
            writeHtml(WFileAdaptor(self.wfile))
        else:
            writeHtml(self.wfile)
        self.wfile.close()

    def log_message(self, format, *args):
        global glb_verbose
        if glb_verbose:
            BaseHTTPRequestHandler.log_message(self, format, *args)

class HttpServerThread(threading.Thread):
    def __init__(self, port):
        self.port = port
        threading.Thread.__init__(self)

    def run(self):
        global glb_webReadyEvent, glb_webPort
        server = TrackerHttpServer(('', self.port))

        # Record the assigned port number.
        self.port = server.server_port
        glb_webPort = self.port

        printMsg('Start http server port ' + str(self.port))

        # Notify interested parties that the
        # tracker's web server thread is ready.
        glb_webReadyEvent.set()

        server.serve_forever()

class ListenThread(threading.Thread):
    def __init__(self, port):
        self.port = port
        threading.Thread.__init__(self)

    def run(self):
        global glb_listenReadyEvent, glb_port
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('', self.port))
        s.listen(20)

        # Record the assigned port number.
        self.port = s.getsockname()[1]
        glb_port = self.port

        printMsg("Start the server, port " + str(self.port))

        # Notify interested parties that the
        # tracker is now listening for connections.
        glb_listenReadyEvent.set()

        while 1:
            incoming = s.accept()
            glb_readqueue.put(incoming)

class ReadThread(threading.Thread):
    def run(self):
        while 1:
            (cs, addr) = glb_readqueue.get()
            printMsg('Connect from ' + str(addr))

            (ip, port) = addr
            if ip == '127.0.0.1':
                # The machine the tracker is on has requested a port
                # We don't want to keep 127.0.0.1 as our callback
                # address or no-one else will be able to contact
                # us.
                ip = socket.gethostbyname(socket.gethostname())
                addr = (ip, port)
            
            data = readmessage(cs)
            cs.close()

            if len(data) > 0:
                printMsg('Read message ' + data)
                glb_msgqueue.put( (addr, data) )

class WriteThread(threading.Thread):
    def run(self):
        while 1:
            (peer, message) = glb_writequeue.get()
            sendmessage(peer, message)

def readmessage(s):
    lengthdata = b''
    while len(lengthdata) < 4:
        chunk = s.recv(4 - len(lengthdata))
        if chunk == b'':
            printMsg('Error in reading length of message')
            raise RuntimeError("Socket broken")
        lengthdata = lengthdata + chunk
    (length,) = struct.unpack('!L', lengthdata)
    printMsg("Read a message length " + str(length))

    # Sanity test the length.  We only expect short messages sent
    # to the tracker.  Further, if someone misconfigures their
    # webbrowser to point to the tracker we don't want to block
    # forever on a read here.
    if length >= 16777216:
        return ''

    data = b''
    while len(data) < length:
        chunk = s.recv(length - len(data))
        if chunk == b'':
            printMsg('Error reading buffer, bytes: {} of {} body is {}'.format(
                len(data), length, data))
            raise RuntimeError("Socket broken")
        data = data + chunk

    # Send an ack.
    s.send(b'j')

    # This should be '' to mark the pipe
    # closing.
    finallyclosed = s.recv(1)

    return data.decode('utf-8')

def writemessage(s, data):
    printMsg("Send message, length " + str(len(data)))
    lengthdata = struct.pack('!L', len(data))
    if sys.version_info.major >= 3:
        lengthdata += bytes(data, 'utf-8')
    else:
        lengthdata += data

    totalsent = 0
    while totalsent < len(lengthdata):
        sent = s.send(lengthdata[totalsent:])
        if sent == 0:
            raise RuntimeError("Socket broken")
        totalsent = totalsent + sent

    # Wait for an ack...
    ack = s.recv(1)
    if ack == b'':
        printMsg('Error in reading ack')
        raise RuntimeError("Socket broken")
    if ack != b'j':
        printMsg('Ack is not the right format!')
        raise RuntimeError("Socket broken")

def postmessage(peer, data):
    glb_writequeue.put( (peer, data) )

def sendmessage(peer, data):
    # Reply
    printMsg("Send message to " + str(peer))
    rs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rs.connect( (peer['address'], peer['port']) )

    writemessage(rs, data)
    rs.close()

def sendclientlist(client, list):
    # Header for the message
    message = 'c'
    for cs in list:
        message = message + cs['address'] + ' '
        message = message + str(cs['port']) + ' '
        message = message + str(cs['proc']) + ' '
    postmessage(client, message)

def serve(port, webport):
    """ Starts the tracker and handles communication between
        peer machines.
    """
    global glb_msgqueue
    global joblist, glb_donelist, glb_joblock
    global glb_barrierlist, glb_barrierdone

    # Start the http server.
    http_thread = HttpServerThread(webport)
    http_thread.daemon = True
    http_thread.start()

    # Start listening.
    tracker_thread = ListenThread(port)
    tracker_thread.daemon = True
    tracker_thread.start()

    printMsg('To shutdown, Ctrl-Z and kill %1')

    # Start our read and write thread pools
    # Because send() and recv() can block for long times we don't want
    # to starve other processes.
    for x in range ( 10 ):
        reader_thread = ReadThread()
        reader_thread.daemon = True
        reader_thread.start()
        writer_thread = WriteThread()
        writer_thread.daemon = True
        writer_thread.start()

    while 1:
        # The main loop only has to worry about processing messages
        # on the glb_msgqueue
        # We have a single thread processing these results because
        # otherwise we'd have to lock everything anyways.
        (addr, data) = glb_msgqueue.get()

        if data == "quit":
            break
        if data == "noop":
            continue

        printMsg("Process message " + str(data))
        (stype, sport, sproc, snproc, jobname) = data.split(' ', 4)

        port = int(sport)
        proc = int(sproc)
        nproc = int(snproc)

        # Handle barrier commands
        if stype == 'barrierinc':
            barrier = glb_barrierlist.get(jobname, { 'val':proc, 'settime':default_timer(), 'proclist':[] })
            val = barrier['val']
            p = { 'address':addr[0], 'port':port }
            message = 'b' + struct.pack('!L', val)
            postmessage(p, message)

            # So barrierset works as expected when we fall through
            proc = val + 1
            stype = 'barrierset'
            # Fall through

        if stype == 'barrierset':
            barrier = glb_barrierlist.get(jobname, { 'val':-1, 'settime':default_timer(), 'proclist':[] })
            barrier['val'] = proc
            barrier['settime'] = default_timer()
            list = barrier['proclist']
            for p in list:
                if p['waitval'] <= proc:
                    postmessage(p, 'd for Done')
                    p['donetime'] = default_timer()
                    glb_barrierdone.append(p)
                    list.remove(p)
            barrier['proclist'] = list
            glb_barrierlist[jobname] = barrier
            continue
        elif stype == 'barrierwait':
            barrier = glb_barrierlist.get(jobname, { 'val':-1, 'settime':default_timer(), 'proclist':[] } )
            # Check for trivially done.
            p = { 'jobname':jobname, 'address':addr[0], 'port':port, 'acquiretime':default_timer(), 'waitval':proc, 'donetime':-1 }
            if proc <= barrier['val']:
                p['donetime'] = default_timer()
                postmessage(p, 'd for Done')
                glb_barrierdone.append(p)
            else:
                list = barrier['proclist']
                list.append(p)
                barrier['proclist'] = list
            glb_barrierlist[jobname] = barrier
            continue

        glb_joblock.acquire()
        job = joblist.get(jobname, {'nproc':0, 'done':0, 'error':False, 'starttime':default_timer(), 'synctime':-1, 'donetime':-1, 'proclist':[]})

        job['nproc'] = nproc
        list = job['proclist']

        if job['error']:
            # This job is in error state, ignore incoming messages.  We sent
            # the error kill code already so these are just left over
            # done messages that shouldn't be expecting replies.
            printMsg('Message to an errored job is ignored')
        elif stype == 'done':
            # This is a done message, increment...
            # Note that because we use a reader queue, the job
            # might not be in the connection list yet!  This
            # means setting the done timer will fail and the
            # job will lock at pending.
            job['done'] = job['done'] + 1
            for p in list:
                if p['proc'] == proc:
                    # Set our done time for this entry...
                    p['donetime'] = default_timer()
            job['proclist'] = list

            if job['done'] >= nproc:
                # Everyone done, broadcast complete message.
                for p in list:
                    postmessage(p, "d for Done!")
                # Remove the job as it is now complete.
                job['jobname'] = jobname
                job['donetime'] = default_timer()
                glb_donelist.append(job)
                del joblist[jobname]
            else:
                joblist[jobname] = job
        elif stype == 'error':
            # Error message, broad cast to everyone the new error state
            for p in list:
                postmessage(p, "e for Error!")
            # Flag the job as errored.
            job['error'] = True
            joblist[jobname] = job
        else:
            # Still in connection phase.
            list.append( { 'proc':proc, 'port':port, 'address':addr[0], 'acquiretime':default_timer(), 'donetime':-1 } )
            if len(list) >= nproc:
                # Everyone connected, send client list
                for p in list:
                    sendclientlist(p, list)
                job['synctime'] = default_timer()
            # Update the job list
            job['proclist'] = list
            joblist[jobname] = job

        printMsg('Processed job list ' + str(joblist))
        glb_joblock.release()

    # Close the I/O resources in case the other threads have a handle
    # on them.  This is important on Windows if simtracker.py is
    # spawned as a subprocess (i.e. subprocess.Popen()) with stdout/stderr 
    # piped to files on disk.  sys.exit(0) does not seem to properly close 
    # resources held open by other threads.
    sys.stdout.flush()
    sys.stderr.flush()
    sys.stdout.close()
    sys.stderr.close()

    # Notify observers that tracking is complete.
    glb_doneEvent.set()

if __name__ == "__main__":
    import optparse

    # Parse command-line arguments.
    usage = 'usage: %prog [options] trackerport webport'
    parser = optparse.OptionParser(usage=usage)
    parser.add_option("-v", "--verbose", dest="verbose", 
                      help="Turn on verbose output.", default=False,
                      action="store_true")
    options, args = parser.parse_args()
   
    # Turn on/off verbosity.
    setVerbosity(options.verbose)

    if len(args) < 2:
        parser.error('Both tracker and web port must be specified.')

    port = int(args[0])
    webport = int(args[1])

    serve(port, webport)
