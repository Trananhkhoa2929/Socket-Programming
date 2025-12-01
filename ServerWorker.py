from random import randint
import sys, traceback, threading, socket
import time

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        
    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:            
            data = connSocket.recv(256)
            if data:
                print("Data received:\n" + data.decode("utf-8"))
                self.processRtspRequest(data.decode("utf-8"))
    
    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # Get the request type
        request = data.split('\n')
        line1 = request[0]. split(' ')
        requestType = line1[0]
        
        # Get the media file name
        filename = line1[1]
        
        # Get the RTSP sequence number 
        seq = request[1]. split(' ')
        
        # Process SETUP request
        if requestType == self. SETUP:
            if self.state == self. INIT:
                # Update state
                print("processing SETUP\n")
                
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                    print(f"File not found: {filename}")
                    return  # FIX: Return sau khi gửi lỗi 404
                
                # Generate a randomized RTSP session ID
                self. clientInfo['session'] = randint(100000, 999999)
                
                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq[1])
                
                # Get the RTP/UDP port from the last line
                self.clientInfo['rtpPort'] = request[2].split(' ')[3]
        
        # Process PLAY request         
        elif requestType == self.PLAY:
            if self.state == self. READY:
                print("processing PLAY\n")
                self.state = self. PLAYING
                
                # Create a new socket for RTP/UDP
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                self.replyRtsp(self.OK_200, seq[1])
                
                # Create a new thread and start sending RTP packets
                self.clientInfo['event'] = threading.Event()
                self. clientInfo['worker'] = threading.Thread(target=self.sendRtp) 
                self. clientInfo['worker']. start()
        
        # Process PAUSE request
        elif requestType == self. PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                
                # FIX: Check if event exists before setting
                if 'event' in self.clientInfo:
                    self. clientInfo['event']. set()
            
                self.replyRtsp(self. OK_200, seq[1])
        
        # Process TEARDOWN request
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")

            # FIX: Check if event exists before setting
            if 'event' in self.clientInfo:
                self.clientInfo['event'].set()
            
            self.replyRtsp(self.OK_200, seq[1])
            
            # FIX: Check if rtpSocket exists before closing
            if 'rtpSocket' in self.clientInfo:
                self.clientInfo['rtpSocket'].close()
            
    def sendRtp(self):
        """Send RTP packets over UDP."""
        while True:
            self.clientInfo['event'].wait(0.03)  # Approximately 30 fps
            
            # Stop sending if request is PAUSE or TEARDOWN
            if self.clientInfo['event'].isSet(): 
                break 
                
            data = self. clientInfo['videoStream'].nextFrame()
            if data: 
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                try:
                    address = self.clientInfo['rtspSocket'][1][0]
                    port = int(self.clientInfo['rtpPort'])
                    
                    # HD Support: Use makeRtp which returns list of packets
                    packets = self.makeRtp(data, frameNumber)
                    
                    # Send each packet (may be multiple if fragmented)
                    for packet in packets:
                        self.clientInfo['rtpSocket'].sendto(packet, (address, port))
                        # Small delay between fragments to avoid congestion
                        time.sleep(0.001)
                        
                except:
                    print("Connection Error")
                    #print('-'*60)
                    #traceback.print_exc(file=sys.stdout)
                    #print('-'*60)

    def makeRtp(self, payload, frameNbr):
        """RTP-packetize the video data with HD fragmentation support."""
        
        frameSize = len(payload)
        
        # If frame is small enough, send as single packet (basic requirement)
        if frameSize <= 1400:
            version = 2
            padding = 0
            extension = 0
            cc = 0
            marker = 1  # Complete frame
            pt = 26  # MJPEG type
            seqnum = frameNbr
            ssrc = 0
            
            rtpPacket = RtpPacket()
            rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
            
            return [rtpPacket. getPacket()]  # Return as list with 1 packet
        
        # HD Support: Fragment large frames
        else:
            fragments = RtpPacket. fragmentFrame(payload, frameNbr)
            return fragments  # Return list of multiple packets
        
    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket. send(reply.encode())
        
        # Error messages
        elif code == self. FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")