from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from collections import deque
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

MIN_BUFFER = 5
FRAME_RATE = 30
FRAME_PERIOD = 1.0 / FRAME_RATE

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT
    
    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3
    DESCRIBE = 4
    
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
        
        # Frame buffer
        self.frameBuffer = deque()
        self.bufferLock = threading.Lock()
        self. bufferReady = threading.Event()
        
        # Fragment reassembly
        self.fragmentBuffer = {}
        self.fragmentLock = threading. Lock()
        
        # Playback control
        self.playEvent = threading.Event()
        
        # Statistics cho HD streaming
        self.totalBytes = 0
        self.startTime = None
        self.displayCount = 0
        
    def createWidgets(self):
        """Build GUI giống demo giảng viên."""
        self.master.title("RTPClient")
        
        # Video display
        self. label = Label(self.master, height=20, bg='black')
        self. label.grid(row=0, column=0, columnspan=5, sticky=W+E+N+S, padx=5, pady=5)
        
        # Time label
        self.timeLabel = Label(self.master, text="00:00", font=("Arial", 12))
        self.timeLabel.grid(row=1, column=0, columnspan=2, padx=5, pady=5)
        
        # Describe button
        self. describeBtn = Button(self. master, width=10, padx=3, pady=3)
        self.describeBtn["text"] = "Describe"
        self. describeBtn["command"] = self.describeMovie
        self.describeBtn.grid(row=1, column=4, padx=2, pady=2)
        
        # Control buttons
        self. setup = Button(self.master, width=10, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self. setup["command"] = self.setupMovie
        self.setup.grid(row=2, column=0, padx=2, pady=2)
        
        self.start = Button(self. master, width=10, padx=3, pady=3)
        self.start["text"] = "Play"
        self. start["command"] = self.playMovie
        self. start.grid(row=2, column=1, padx=2, pady=2)
        
        self.pause = Button(self.master, width=10, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self. pause["command"] = self.pauseMovie
        self.pause.grid(row=2, column=2, padx=2, pady=2)
        
        self.teardown = Button(self.master, width=10, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self. exitClient
        self.teardown.grid(row=2, column=3, padx=2, pady=2)
    
    def describeMovie(self):
        """Describe button - hiển thị thông tin stream."""
        if self.state != self.INIT:
            self.sendRtspRequest(self. DESCRIBE)
    
    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
    
    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        # In data rate khi kết thúc
        self.printStatistics()
        self.master.destroy()
        try:
            os. remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)
            # In data rate khi pause
            self.printStatistics()
    
    def printStatistics(self):
        """In thống kê video data rate."""
        if self.startTime and self.totalBytes > 0:
            elapsed = time.time() - self.startTime
            if elapsed > 0:
                dataRate = self.totalBytes / elapsed
                print(f"[*]Video data rate: {int(dataRate)} bytes/sec")
    
    def playMovie(self):
        """Play button handler."""
        if self.state == self. READY:
            self. playEvent.clear()
            self.bufferReady.clear()
            self. displayCount = 0
            self.totalBytes = 0
            self.startTime = time.time()
            
            with self.bufferLock:
                self.frameBuffer.clear()
            
            with self.fragmentLock:
                self. fragmentBuffer.clear()
            
            threading.Thread(target=self.listenRtp, daemon=True). start()
            threading.Thread(target=self.playFromBuffer, daemon=True).start()
            
            self.sendRtspRequest(self.PLAY)
    
    def listenRtp(self):        
        """Listen for RTP packets."""
        while True:
            try:
                data = self.rtpSocket.recv(65535)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    seq = rtpPacket.seqNum()
                    try:
                        marker = rtpPacket.marker()
                    except:
                        marker = 1
                    payload = rtpPacket.getPayload()
                    
                    # Non-fragmented frame
                    if seq < 100 and marker == 1:
                        currFrameNbr = seq
                        if currFrameNbr > self.frameNbr:
                            self.addToBuffer(currFrameNbr, payload)
                            # Thống kê bytes
                            self. totalBytes += len(payload)
                        continue
                    
                    # Fragmented frames (HD support)
                    frameNbr = seq // 100
                    fragIdx = seq % 100
                    
                    with self.fragmentLock:
                        if frameNbr not in self.fragmentBuffer:
                            self. fragmentBuffer[frameNbr] = {}
                        self.fragmentBuffer[frameNbr][fragIdx] = payload
                        
                        if marker == 1:
                            frags = self.fragmentBuffer. get(frameNbr, {})
                            if frags:
                                assembled = b''.join(frags[i] for i in sorted(frags.keys()))
                                del self.fragmentBuffer[frameNbr]
                                
                                if frameNbr > self.frameNbr:
                                    self.addToBuffer(frameNbr, assembled)
                                    # Thống kê bytes
                                    self.totalBytes += len(assembled)
                    
            except socket.timeout:
                continue
            except:
                if self.playEvent.is_set(): 
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket. shutdown(socket.SHUT_RDWR)
                        self. rtpSocket.close()
                    except:
                        pass
                    break
    
    def addToBuffer(self, frameNbr, payload):
        """Add frame to buffer."""
        with self.bufferLock:
            self.frameBuffer.append((frameNbr, payload))
            bufferSize = len(self.frameBuffer)
            
            if bufferSize >= MIN_BUFFER and not self.bufferReady.is_set():
                self. bufferReady. set()
    
    def updateTime(self):
        """Cập nhật thời gian phát."""
        if self.startTime:
            elapsed = int(time.time() - self.startTime)
            minutes = elapsed // 60
            seconds = elapsed % 60
            self.timeLabel. configure(text=f"{minutes:02d}:{seconds:02d}")
    
    def playFromBuffer(self):
        """Play frames từ buffer."""
        print(f"Buffering...  waiting for {MIN_BUFFER} frames")
        self.bufferReady.wait()
        print("Starting playback...")
        
        lastFrameTime = time.time()
        
        while True:
            if self.playEvent.is_set() or self.teardownAcked == 1:
                break
            
            frame = None
            with self. bufferLock:
                if len(self.frameBuffer) > 0:
                    frameNbr, payload = self.frameBuffer.popleft()
                    frame = (frameNbr, payload)
            
            if frame:
                frameNbr, payload = frame
                
                # Timing control
                currentTime = time.time()
                elapsed = currentTime - lastFrameTime
                sleepTime = FRAME_PERIOD - elapsed
                
                if sleepTime > 0:
                    time.sleep(sleepTime)
                
                lastFrameTime = time.time()
                
                if frameNbr > self.frameNbr:
                    self.frameNbr = frameNbr
                    self.displayCount += 1
                    
                    # In Current Seq Num giống demo
                    print(f"Current Seq Num: {frameNbr}")
                    
                    self.updateMovie(self.writeFrame(payload))
                    
                    # Cập nhật thời gian
                    self.master.after(0, self.updateTime)
            else:
                time.sleep(0.001)
                    
    def writeFrame(self, data):
        """Write frame to temp file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as file:
            file. write(data)
        return cachename
    
    def updateMovie(self, imageFile):
        """Update video frame in GUI."""
        try:
            photo = ImageTk. PhotoImage(Image. open(imageFile))
            self.label.configure(image=photo, height=288) 
            self. label.image = photo
        except:
            pass
        
    def connectToServer(self):
        """Connect to the Server."""
        self. rtspSocket = socket.socket(socket.AF_INET, socket. SOCK_STREAM)
        try:
            self. rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)
    
    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""    
        
        if requestCode == self. SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1. 0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Transport: RTP/UDP; client_port= {self.rtpPort}\r\n"
            self.requestSent = self. SETUP
        
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1. 0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.PLAY
        
        elif requestCode == self. PAUSE and self.state == self.PLAYING:
            self. rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self. rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self. PAUSE
            
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            self. rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.TEARDOWN
            
        elif requestCode == self.DESCRIBE:
            self.rtspSeq += 1
            request = f"DESCRIBE {self.fileName} RTSP/1.0\r\n"
            request += f"CSeq: {self.rtspSeq}\r\n"
            request += f"Session: {self.sessionId}\r\n"
            self.requestSent = self.DESCRIBE
        else:
            return
        
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
        self.rtpSocket = socket. socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket. settimeout(0.5)
        self.rtpSocket. setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        
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