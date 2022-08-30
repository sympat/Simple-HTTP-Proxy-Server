from socket import *
from urllib.parse import urlparse
import threading
import sys
import traceback
from datetime import datetime, timedelta

BUFSIZE = 2048
TIMEOUT = 0.5
CRLF = '\r\n'

# these are my-defined Error Classes
class UnExpectedDataEnter(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg

class CloseConnection(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg

class NotSupportMethod(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg

class NotSupportPC(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg

class NotSupportArgv(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


# Dissect HTTP header into line(first line), header(second line to end), body
def parseHTTP(data):
    non_body = data.split(b'\r\n\r\n')[0]
    body = data[len(non_body)+4:]

    line = non_body.decode().split('\r\n')[0]

    header_list = non_body.decode().split('\r\n')[1:]
    header = {}
    for element in header_list:
        field = element.split(': ')[0]
        value = element.split(': ')[1]
        header[field] = value

    return HTTPPacket(line, header, body)


# Receive HTTP packet with socket from conn
# It support seperated packet receive
def recvData(conn):
    try:
        # Set time out for error or persistent connection end
        conn.settimeout(TIMEOUT)
        data = conn.recv(BUFSIZE)
        if not data:
            raise UnExpectedDataEnter("UnExpectedDataEnter")
        while b'\r\n\r\n' not in data:
            data += conn.recv(BUFSIZE)
        packet = parseHTTP(data)
        body = packet.body

        # Chunked-Encoding
        if packet.isChunked():
            readed = 0
            while True:
                while b'\r\n' not in body[readed:len(body)]:
                    d = conn.recv(BUFSIZE)
                    body += d
                size_str = body[readed:len(body)].split(b'\r\n')[0]
                size = int(size_str, 16)
                readed += len(size_str) + 2
                while len(body) - readed < size + 2:
                    d = conn.recv(BUFSIZE)
                    body += d
                readed += size + 2
                if size == 0: break
        
        # Content-Length
        elif packet.getHeader('Content-Length'):
            received = 0
            expected = packet.getHeader('Content-Length')
            if expected == None:
                expected = '0'
            expected = int(expected)
            received += len(body)
            
            while received < expected:
                d = conn.recv(BUFSIZE)
                received += len(d)
                body += d
        
        packet.body = body
        return packet.pack()
    except Exception:
        raise


# HTTP packet class
# Manage packet data and provide related functions
class HTTPPacket:
    # Constructer
    def __init__(self, line, header, body):
        self.line = line  # Packet first line(String)
        self.header = header  # Headers(Dict.{Field:Value})
        self.body = body  # Body(Bytes)
    
    # Make encoded packet data
    def pack(self):
        ret = self.line + CRLF
        for field in self.header:
            ret += field + ': ' + self.header[field] + CRLF
        ret += CRLF
        ret = ret.encode()
        ret += self.body
        return ret
    
    # Get HTTP header value
    # If not exist, return empty string
    def getHeader(self, field):
        if field in self.header:
            return self.header[field]
        else:
            return ""
    
    # Set HTTP header value
    # If not exist, add new field
    # If value is empty string, remove field
    def setHeader(self, field, value):
        if self.header[field]:
            self.header[field] = value
        if value == "":
            del self.header[field]
    
    # Get URL from request packet line
    def getURL(self):
        url = self.line.split(" ")[1]
        return url
    
    # Set URL from request packet line
    def setURL(self, url):
        self.line = self.line.split(" ")[0] + " " + url + " " + self.line.split(" ")[2]

    # Get METHOD from request packet line
    def getMethod(self):
        method = self.line.split(" ")[0]
        return method

    # Check whether this http msg uses Chunked Encoding
    def isChunked(self):
        return 'chunked' in self.getHeader('Transfer-Encoding')

    # Set Content-Length from packet
    def setContentLength(self):
        content_length = 0

        if self.getHeader("Content-Length"):
            content_length = int(self.getHeader("Content-Length"))
        else:
            chunk = self.body
            while chunk:
                size = chunk.split(b'\r\n')[0]
                content_length += int(size,16)
                chunk = chunk[len(size)+2:]
                chunk = chunk[int(size,16)+2:]

        return content_length


# Proxy handler thread class
class ProxyThread(threading.Thread):
    # variable for thread RLock
    thread_lock = threading.RLock()
    # variable for thread connection no.
    conn_num = 0
    # variables for MT and PC mode
    isPC = False
    isMT = False

    def __init__(self, conn, addr):
        super().__init__()
        self.conn = conn  # Client socket
        self.addr = addr  # Client address
    
    # Thread Routine
    def run(self):
        # variable for server socket
        svr = None
        # variable for check where this loop is first loop
        is_first_loop = True
        # variable for check where this thread currently hold lock
        is_locked = False

        while True:
            try:
                # receive data from the client and evaluate request arrival time
                # if timeout occur and unexpected data is entered, raise Exception
                data = recvData(self.conn)
                req_arrival_time = datetime.now()

                # only MT mode is OFF and this loop is first loop,
                # if any thread currently hold lock get lock, else wait for release
                if is_first_loop and not ProxyThread.isMT:
                    ProxyThread.thread_lock.acquire()
                    is_locked = True

                # receive data from the client
                req = parseHTTP(data)
                original_req_line = req.line

                # if request msg uses HTTPS Connection, raise Exception (HTTPS Connection is not supported in this simple version)
                if req.getMethod() == "CONNECT":
                    # only MT mode is ON, if any thread currently hold lock get lock, else wait for release
                    if ProxyThread.isMT:
                        ProxyThread.thread_lock.acquire()

                    # write information in terminal
                    ProxyThread.conn_num += 1
                    print("[%d] %s" % (ProxyThread.conn_num, req_arrival_time.strftime("%d/%b/%Y %H:%M:%S.%f")[:-3]))
                    print("[%d] > Connection from %s:%s" %(ProxyThread.conn_num, self.addr[0], self.addr[1]))
                    print("[%d] > %s" % (ProxyThread.conn_num, original_req_line))
                    print()

                    # only MT mode is ON, release lock
                    if ProxyThread.isMT:
                        ProxyThread.thread_lock.release()
                    raise NotSupportMethod("NotSupportMethod")

                # Reassemble url
                reassemble_url = ""
                url = urlparse(req.getURL())
                if url[2]:
                    reassemble_url += url[2]
                if url[3]:
                    reassemble_url += ";" + url[3]
                if url[4]:
                    reassemble_url += "?" + url[4]
                if url[5]:
                    reassemble_url += "#" + url[5]
                req.setURL(reassemble_url)

                # Remove proxy infomation
                proxy_info = req.getHeader('Proxy-Connection')

                if proxy_info:
                    print(proxy_info)
                    req.setHeader('Connection', proxy_info)
                    req.setHeader('Proxy-Connection', "")


                # if this loop is first loop, connect to server
                if is_first_loop:
                    svr = socket(AF_INET, SOCK_STREAM)
                    svr.connect((url[1], 80))


                # send a client's request to the server
                svr.sendall(req.pack())


                # receive data from the server
                # if timeout occur and unexpected data is entered, raise Exception
                data = recvData(svr)
                res = parseHTTP(data)

                # only MT mode is ON, if any thread currently hold lock get lock, else wait for release
                if ProxyThread.isMT:
                    ProxyThread.thread_lock.acquire()

                # write information in terminal
                ProxyThread.conn_num += 1
                print("[%d] %s" % (ProxyThread.conn_num, req_arrival_time.strftime("%d/%b/%Y %H:%M:%S.%f")[:-3]))
                print("[%d] > Connection from %s:%s" %(ProxyThread.conn_num, self.addr[0], self.addr[1]))
                print("[%d] > %s" % (ProxyThread.conn_num, original_req_line))
                print("[%d] < %s" % (ProxyThread.conn_num, res.line))
                print("[%d] < %s %dbytes" % (ProxyThread.conn_num, res.getHeader("Content-Type"), res.setContentLength()))
                print()

                # only MT mode is ON, release lock
                if ProxyThread.isMT:
                    ProxyThread.thread_lock.release() 

                # send received data to client 
                self.conn.sendall(res.pack())

                # if this loop is first loop, now set is_first_loop False
                if is_first_loop:
                    is_first_loop = False

                # if any sockets connected to proxy server send "Connection : close" header, raise Exception
                if req.getHeader("Connection") == "close" or res.getHeader("Connection") == "close":
                    raise CloseConnection("CloseConnection")

                # if PC mode is OFF, rasise Exception
                if not ProxyThread.isPC:
                    raise NotSupportPC("NotSupportPC")
            except Exception:
                # if any exception occur, close server and client socket in proxy server.
                if svr:
                    svr.close()
                self.conn.close()

                # only MT mode is OFF, if exception occured Thread has lock, release lock
                if is_locked and not ProxyThread.isMT:
                    ProxyThread.thread_lock.release()
                    is_locked = False

                # break while loop so that this thread is terminated
                break


def main():
    try:
        host = "0.0.0.0"
        port = int(sys.argv[1])

        # parse sys argument
        if len(sys.argv) == 2:
            isMT = False
            isPC = False
        elif len(sys.argv) == 3 and sys.argv[2] == '-mt':
            isMT = True
            isPC = False
        elif len(sys.argv) == 3 and sys.argv[2] == '-pc':
            isMT = False
            isPC = True
        elif len(sys.argv) == 4 and sys.argv[2] == '-mt' and sys.argv[3] == '-pc':
            isMT = True
            isPC = True
        else:
            raise NotSupportArgv('Usage : python3 proxy.py port [-mt|-pc|-mt -pc]')

        # set PC, MT mode value
        if isPC:
            ProxyThread.isPC = isPC
        if isMT:
            ProxyThread.isMT = isMT

        sock = socket(AF_INET, SOCK_STREAM)
        sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(20)

        # write information in terminal
        print('Proxy Server started on port %d at %s' % (port, datetime.now().strftime("%d/%b/%Y %H:%M:%S.%f")[:-3]))
        print("* Multithreading - [%s]" % ("ON" if isMT else "OFF"))
        print("* Persistent Connection - [%s]" % ("ON" if isPC else "OFF"))
        print()

        # until some excpetion including KeyboardIntterrupt(Ctrl+C) occur, loop infinitly
        while True:
            # Client connect
            conn, addr = sock.accept()
            # Start Handling
            pt = ProxyThread(conn, addr)
            # connection thread start
            pt.start()
    except NotSupportArgv as e:
        print(e)
        sys.exit()
    except KeyboardInterrupt as e:
        print(e)
        sock.close()
        sys.exit()


if __name__ == '__main__':
    main()
