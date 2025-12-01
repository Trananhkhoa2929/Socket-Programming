from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, os

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2

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
        self.requestSent = -1
        self.teardownAcked = 0

        self.state = self.INIT
        self.frameNbr = 0

        # For fragment reassembly
        self.fragmentBuffer = {}      # { frameNbr: {fragIdx: payload, ...}, ... }
        self.fragmentLock = threading.Lock()

        self.connectToServer()


    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        if self.state == self.READY:
            # start listening thread before sending PLAY so we don't miss packets
            threading.Thread(target=self.listenRtp, daemon=True).start()
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """
        Receive RTP packets and handle:
        - if server sends full-frame in one packet (seq = frameNbr), display immediately
        - if server fragments frames, reconstruct using seq encoding and marker bit:
            * Assume server encodes seq such that: seq = frameNbr*100 + fragIdx (fragIdx small int)
            * marker() == 1 indicates last fragment for that frame
        """
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if not data:
                    continue
                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                seq = rtpPacket.seqNum()
                try:
                    marker = rtpPacket.marker()
                except:
                    marker = 0
                payload = rtpPacket.getPayload()

                # Heuristic 1: server sends whole frame in one RTP (seq is frame number, marker may be 0)
                if marker == 0 and seq > 0 and seq <= 10000 and len(payload) > 0:
                    # Check if looks like whole-frame: seq is small and server logs showed seq==frameNbr previously.
                    # But if server implements fragmentation it will normally set marker for last fragment.
                    # We treat this as a whole frame only if fragmentBuffer doesn't indicate fragmentation scheme.
                    # To be safe: if seq not encoded with frag idx (seq < 100), treat as full-frame.
                    if seq < 100:
                        currFrameNbr = seq
                        print("Received single-packet frame:", currFrameNbr)
                        if currFrameNbr > self.frameNbr:
                            self.frameNbr = currFrameNbr
                            self.updateMovie(self.writeFrame(payload))
                        continue

                # Otherwise, assume fragmentation scheme: seq = frameNbr*100 + fragIdx
                frameNbr = seq // 100
                fragIdx = seq % 100

                with self.fragmentLock:
                    if frameNbr not in self.fragmentBuffer:
                        self.fragmentBuffer[frameNbr] = {}
                    self.fragmentBuffer[frameNbr][fragIdx] = payload

                    # If this is the last fragment (marker == 1), reassemble and display
                    if marker == 1:
                        frags = self.fragmentBuffer.get(frameNbr, {})
                        if not frags:
                            continue
                        # Reassemble fragments in order of fragIdx
                        assembled = b''.join(frags[i] for i in sorted(frags.keys()))
                        # Clean up buffer for that frame
                        del self.fragmentBuffer[frameNbr]

                        print(f"Reassembled frame {frameNbr} from {len(frags)} fragments (seq={seq})")
                        if frameNbr > self.frameNbr:
                            self.frameNbr = frameNbr
                            self.updateMovie(self.writeFrame(assembled))

            except Exception as e:
                # Stop listening on pause or teardown
                if hasattr(self, 'playEvent') and self.playEvent.isSet():
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket.shutdown(socket.SHUT_RDWR)
                        self.rtpSocket.close()
                    except:
                        pass
                    break
                # Otherwise continue listening (ignore transient errors)
                # print("listenRtp error:", e)
                continue

    def writeFrame(self, data):
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as file:
            file.write(data)
        return cachename

    def updateMovie(self, imageFile):
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', f"Connection to '{self.serverAddr}' failed.")

    def sendRtspRequest(self, requestCode):
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            request = f"SETUP {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nTransport: RTP/UDP; client_port= {self.rtpPort}\r\n\r\n"
            self.requestSent = self.SETUP
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = f"PLAY {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n\r\n"
            self.requestSent = self.PLAY
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = f"PAUSE {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n\r\n"
            self.requestSent = self.PAUSE
        elif requestCode == self.TEARDOWN and self.state != self.INIT:
            self.rtspSeq += 1
            request = f"TEARDOWN {self.fileName} RTSP/1.0\r\nCSeq: {self.rtspSeq}\r\nSession: {self.sessionId}\r\n\r\n"
            self.requestSent = self.TEARDOWN
        else:
            return

        try:
            self.rtspSocket.sendall(request.encode())
            print('\nData sent:\n' + request)
        except:
            tkMessageBox.showwarning('Send Failed', 'Failed to send RTSP request.')

    def recvRtspReply(self):
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode("utf-8"))
            if self.requestSent == self.TEARDOWN:
                try:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    self.rtspSocket.close()
                except:
                    pass
                break

    def parseRtspReply(self, data):
        lines = data.split('\n')
        if len(lines) < 3:
            return
        try:
            seqNum = int(lines[1].split(' ')[1])
        except:
            return
        if seqNum == self.rtspSeq:
            try:
                session = int(lines[2].split(' ')[1])
            except:
                session = 0
            if self.sessionId == 0:
                self.sessionId = session
            if self.sessionId == session or self.requestSent == self.SETUP:
                statusCode = int(lines[0].split(' ')[1])
                if statusCode == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        if hasattr(self, 'playEvent'):
                            self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
        except:
            tkMessageBox.showwarning('Unable to Bind', f'Unable to bind PORT={self.rtpPort}')

    def handler(self):
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()

if __name__ == "__main__":
    # Usage: python client.py Server_name Server_port RTP_port Video_file
    try:
        serverAddr = sys.argv[1]
        serverPort = sys.argv[2]
        rtpPort = sys.argv[3]
        fileName = sys.argv[4]
    except:
        print("[Usage: client.py Server_name Server_port RTP_port Video_file]\n")
        sys.exit(1)

    from tkinter import Tk
    root = Tk()
    app = Client(root, serverAddr, serverPort, rtpPort, fileName)
    app.master.title("RTPClient")
    root.mainloop()