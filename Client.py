from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from collections import deque
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# Client-Side Caching Configuration
BUFFER_SIZE = 10  # Pre-buffer N frames before playing (đủ để mượt, không quá delay)
FRAME_RATE = 20 # Target FPS
FRAME_PERIOD = 1.0 / FRAME_RATE  # ~33ms per frame

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3
    
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self. requestSent = -1
        self. teardownAcked = 0
        self. connectToServer()
        self.frameNbr = 0
        
        # Client-Side Caching: Frame buffer to reduce jitter
        self.frameBuffer = deque()
        self.bufferLock = threading.Lock()
        self. bufferReady = threading.Event()
        
        # For HD fragment reassembly
        self.fragmentBuffer = {}
        self.fragmentLock = threading. Lock()
        
        # Playback control
        self.playEvent = threading.Event()
        
    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self. setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self. setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)
        
        # Create Play button        
        self.start = Button(self. master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self. start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)
        
        # Create Pause button            
        self.pause = Button(self. master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)
        
        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self. exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)
        
        # Create a label to display the movie
        self.label = Label(self. master, height=19)
        self. label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
        
        # Buffer status label
        self. bufferLabel = Label(self.master, text="Buffer: 0 frames")
        self.bufferLabel. grid(row=2, column=0, columnspan=4, padx=5, pady=2)
    
    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self. SETUP)
    
    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)        
        self.master.destroy()
        try:
            os. remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
    
    def playMovie(self):
        """Play button handler."""
        if self.state == self. READY:
            # Reset events
            self.playEvent.clear()
            self.bufferReady.clear()
            
            # Clear old buffer
            with self.bufferLock:
                self.frameBuffer.clear()
            
            # Start RTP listener thread
            threading.Thread(target=self.listenRtp, daemon=True). start()
            
            # Start playback thread
            threading.Thread(target=self. playFromBuffer, daemon=True).start()
            
            # Send PLAY request
            self.sendRtspRequest(self. PLAY)
    
    def listenRtp(self):        
        """Listen for RTP packets and store in buffer (Client-Side Caching)."""
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    seq = rtpPacket.seqNum()
                    try:
                        marker = rtpPacket.marker()
                    except:
                        marker = 0
                    payload = rtpPacket.getPayload()
                    
                    # Check if this is a single-packet frame (non-fragmented)
                    if seq < 100 and marker == 1:
                        currFrameNbr = seq
                        if currFrameNbr > self.frameNbr:
                            self.addToBuffer(currFrameNbr, payload)
                        continue
                    
                    # Handle fragmented frames (HD streaming support)
                    frameNbr = seq // 100
                    fragIdx = seq % 100
                    
                    with self.fragmentLock:
                        if frameNbr not in self.fragmentBuffer:
                            self. fragmentBuffer[frameNbr] = {}
                        self.fragmentBuffer[frameNbr][fragIdx] = payload
                        
                        # If this is the last fragment (marker == 1), reassemble
                        if marker == 1:
                            frags = self.fragmentBuffer. get(frameNbr, {})
                            if frags:
                                assembled = b''.join(frags[i] for i in sorted(frags.keys()))
                                del self.fragmentBuffer[frameNbr]
                                
                                if frameNbr > self.frameNbr:
                                    self.addToBuffer(frameNbr, assembled)
                    
            except:
                if self.playEvent.isSet(): 
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket. shutdown(socket.SHUT_RDWR)
                        self. rtpSocket.close()
                    except:
                        pass
                    break
    
    def addToBuffer(self, frameNbr, payload):
        """Add frame to buffer (Client-Side Caching)."""
        with self.bufferLock:
            self.frameBuffer.append((frameNbr, payload))
            bufferSize = len(self.frameBuffer)
            
            # Update buffer status display
            self.master.after(0, lambda: self.updateBufferLabel(bufferSize))
            
            # Signal when buffer has enough frames
            if bufferSize >= BUFFER_SIZE and not self.bufferReady.is_set():
                print(f"Buffer ready with {bufferSize} frames")
                self.bufferReady.set()
    
    def updateBufferLabel(self, size):
        """Update buffer label safely from main thread."""
        try:
            self.bufferLabel. configure(text=f"Buffer: {size} frames")
        except:
            pass
    
    def playFromBuffer(self):
        """Play frames from buffer with stable frame rate."""
        # Wait for initial buffering
        print(f"Buffering...  waiting for {BUFFER_SIZE} frames")
        self.bufferReady.wait()
        print("Starting playback from buffer...")
        
        lastTime = time.time()
        
        while True:
            if self.playEvent.isSet() or self.teardownAcked == 1:
                break
            
            # Calculate time to wait for stable frame rate
            currentTime = time.time()
            elapsed = currentTime - lastTime
            sleepTime = FRAME_PERIOD - elapsed
            
            if sleepTime > 0:
                time.sleep(sleepTime)
            
            lastTime = time.time()
            
            # Get frame from buffer
            frame = None
            with self.bufferLock:
                if len(self.frameBuffer) > 0:
                    frameNbr, payload = self.frameBuffer.popleft()
                    frame = (frameNbr, payload)
                    bufferSize = len(self.frameBuffer)
                    self.master.after(0, lambda bs=bufferSize: self.updateBufferLabel(bs))
            
            if frame:
                frameNbr, payload = frame
                if frameNbr > self.frameNbr:
                    self.frameNbr = frameNbr
                    self.updateMovie(self.writeFrame(payload))
            else:
                # Buffer empty - wait a bit for more frames
                time.sleep(0.01)
                    
    def writeFrame(self, data):
        """Write the received frame to a temp image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as file:
            file. write(data)
        return cachename
    
    def updateMovie(self, imageFile):
        """Update the image file as video frame in the GUI."""
        try:
            photo = ImageTk. PhotoImage(Image. open(imageFile))
            self.label.configure(image=photo, height=288) 
            self. label.image = photo
        except:
            pass
        
    def connectToServer(self):
        """Connect to the Server."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket. SOCK_STREAM)
        try:
            self. rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)
    
    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""    
        
        # SETUP request
        if requestCode == self. SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1. 0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Transport: RTP/UDP; client_port= {self.rtpPort}\r\n"
            self.requestSent = self. SETUP
        
        # PLAY request
        elif requestCode == self. PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.PLAY
        
        # PAUSE request
        elif requestCode == self. PAUSE and self. state == self. PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.PAUSE
            
        # TEARDOWN request
        elif requestCode == self. TEARDOWN and not self.state == self.INIT:
            self. rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.TEARDOWN
        else:
            return
        
        # Send the RTSP request
        self.rtspSocket. send(request.encode())
        print('\nData sent:\n' + request)
    
    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply: 
                self.parseRtspReply(reply. decode("utf-8"))
            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.shutdown(socket.SHUT_RDWR)
                self.rtspSocket.close()
                break
    
    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split('\n')
        seqNum = int(lines[1]. split(' ')[1])
        
        if seqNum == self. rtspSeq:
            session = int(lines[2]. split(' ')[1])
            if self.sessionId == 0:
                self.sessionId = session
            
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200: 
                    if self.requestSent == self. SETUP:
                        self.state = self.READY
                        self. openRtpPort() 
                    elif self.requestSent == self. PLAY:
                        self.state = self.PLAYING
                    elif self.requestSent == self. PAUSE:
                        self.state = self.READY
                        self. playEvent.set()
                        self.bufferReady.clear()
                    elif self.requestSent == self. TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1 
    
    def openRtpPort(self):
        """Open RTP socket."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket. settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
        except:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self. rtpPort)

    def handler(self):
        """Handler on closing the GUI window."""
        self. pauseMovie()
        if tkMessageBox.askokcancel("Quit? ", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self. playMovie()