from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import time 
from queue import Queue
from RtpPacket import RtpPacket

# --- CẤU HÌNH BỘ ĐỆM & VIDEO ---
BUFFER_SIZE = 100      # bộ đệm catching
MIN_BUFFER_SIZE = 20   # đủ 20 frames mới play
FPS_TARGET = 20       #

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

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
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		
		# --- BUFFERING & STATS VARIABLES ---
		self.frameBuffer = Queue(maxsize=BUFFER_SIZE)
		self.isBuffering = False
		self.playEvent = threading.Event()
		self.exitEvent = threading.Event()
		
		# Statistics variables
		self.stat_startTime = time.time()
		self.stat_totalBytes = 0
		self.stat_totalPackets = 0
		self.stat_lostPackets = 0
		self.stat_lastSeqNum = -1
		self.stat_framesDisplayed = 0
		self.stat_fps = 0
		
		self.createWidgets()
		
	def createWidgets(self):
		"""Build GUI with Statistics Panel."""
		# Frame chứa nút bấm
		btnFrame = Frame(self.master)
		btnFrame.grid(row=2, column=0, columnspan=4, sticky=W+E)

		self.setup = Button(btnFrame, text="Setup", command=self.setupMovie, width=15)
		self.setup.grid(row=0, column=0, padx=2, pady=2)
		
		self.start = Button(btnFrame, text="Play", command=self.playMovie, width=15)
		self.start.grid(row=0, column=1, padx=2, pady=2)
		
		self.pause = Button(btnFrame, text="Pause", command=self.pauseMovie, width=15)
		self.pause.grid(row=0, column=2, padx=2, pady=2)
		
		self.teardown = Button(btnFrame, text="Teardown", command=self.exitClient, width=15)
		self.teardown.grid(row=0, column=3, padx=2, pady=2)
		
		# Màn hình 
		self.label = Label(self.master, height=19, bg="black", fg="white")
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		self.label.config(text="Click Setup to Start")
		
		# statistics
		statFrame = Frame(self.master, relief=GROOVE, borderwidth=2)
		statFrame.grid(row=1, column=0, columnspan=4, sticky=W+E, padx=5, pady=5)
		
		self.lblFPS = Label(statFrame, text="FPS: 0", width=15, anchor=W)
		self.lblFPS.grid(row=0, column=0)
		
		self.lblLoss = Label(statFrame, text="Loss: 0%", width=15, anchor=W)
		self.lblLoss.grid(row=0, column=1)
		
		self.lblRate = Label(statFrame, text="Rate: 0 kbps", width=15, anchor=W)
		self.lblRate.grid(row=0, column=2)
		
		self.lblBuffer = Label(statFrame, text="Buff: [..........]", width=20, anchor=W, fg="blue")
		self.lblBuffer.grid(row=0, column=3)

	def setupMovie(self):
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		self.sendRtspRequest(self.TEARDOWN)
		self.exitEvent.set()
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
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)
	
	def receiveRtp(self):		
		"""Receive RTP packets, Reassemble, Buffer, and Update Network Stats."""
		current_frame_fragments = []
		
		while True:
			if self.exitEvent.is_set(): break
			try:
				data, addr = self.rtpSocket.recvfrom(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					currSeqNum = rtpPacket.seqNum()
					payload = rtpPacket.getPayload()
					
					self.stat_totalBytes += len(payload) + 12 
					self.stat_totalPackets += 1
					
					if self.stat_lastSeqNum != -1:
						diff = currSeqNum - self.stat_lastSeqNum
						if diff > 1:
							self.stat_lostPackets += (diff - 1)
					self.stat_lastSeqNum = currSeqNum

					current_frame_fragments.append(payload)
					
					if rtpPacket.marker() == 1: 
	
						self.frameNbr += 1
						print(f"Current Frame Num: {self.frameNbr} (Seq: {currSeqNum})")

						full_frame = b''.join(current_frame_fragments)
						current_frame_fragments = []
						
						if not self.frameBuffer.full():
							self.frameBuffer.put(full_frame)
			except socket.timeout:
				continue
			except:
				if self.exitEvent.is_set(): break

	def consumeBuffer(self):
		"""Display frames and Update GUI Stats."""
		last_time = time.time()
		frame_interval = 1.0 / FPS_TARGET
		
		while True:
			if self.exitEvent.is_set(): break

			# Update Stats on GUI every 1 second (approx)
			if time.time() - self.stat_startTime > 1.0:
				self.updateStatsGUI()
				self.stat_startTime = time.time()
				self.stat_totalBytes = 0
				self.stat_framesDisplayed = 0

			if self.state != self.PLAYING:
				time.sleep(0.1)
				continue

			# Intelligent Buffering Logic
			buff_size = self.frameBuffer.qsize()
			if buff_size < MIN_BUFFER_SIZE and self.isBuffering:
				# đang nạp -> chưa phát
				time.sleep(0.1)
				continue
			elif buff_size == 0:
				# Hết buffer -> nạp
				self.isBuffering = True
				print("Buffer empty! Re-buffering...")
				continue
			else:
				# Buffer possible -> Chiếu
				self.isBuffering = False

			if not self.frameBuffer.empty():
				frameData = self.frameBuffer.get()
				self.updateMovie(self.writeFrame(frameData))
				
				# STATS: Count displayed frame
				self.stat_framesDisplayed += 1
				
				# Smooth Playback Timing
				curr_time = time.time()
				diff = curr_time - last_time
				delay = max(0, frame_interval - diff)
				time.sleep(delay)
				last_time = time.time()

	def updateStatsGUI(self):
		"""Update labels with calculated values."""
		# FPS
		self.lblFPS.config(text=f"FPS: {self.stat_framesDisplayed}")
		
		# Packet Loss %
		if self.stat_totalPackets > 0:
			loss_rate = (self.stat_lostPackets / (self.stat_totalPackets + self.stat_lostPackets)) * 100
		else:
			loss_rate = 0
		self.lblLoss.config(text=f"Loss: {loss_rate:.1f}%")
		
		# Data Rate (kbps) = bytes * 8 / 1000
		rate_kbps = (self.stat_totalBytes * 8) / 1000
		self.lblRate.config(text=f"Rate: {rate_kbps:.0f} kbps")
		
		# Buffer Bar
		buff_len = self.frameBuffer.qsize()
		# Max 10 chars for bar
		bar_fill = int((buff_len / BUFFER_SIZE) * 10)
		bar_str = "[" + "=" * bar_fill + "." * (10 - bar_fill) + "]"
		self.lblBuffer.config(text=f"Buf: {bar_str} ({buff_len})")

	def writeFrame(self, data):
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		try:
			with open(cachename, "wb") as file:
				file.write(data)
		except: pass
		return cachename
	
	def updateMovie(self, imageFile):
		try:
			photo = ImageTk.PhotoImage(Image.open(imageFile))
			self.label.configure(image = photo, height=288) 
			self.label.image = photo
		except: pass
		
	def connectToServer(self):
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkinter.messagebox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()
			self.rtspSeq += 1
			request = "SETUP " + self.fileName + " RTSP/1.0\n"
			request += "CSeq: " + str(self.rtspSeq) + "\n"
			request += "Transport: RTP/UDP; client_port= " + str(self.rtpPort)
			self.requestSent = self.SETUP
		
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = "PLAY " + self.fileName + " RTSP/1.0\n"
			request += "CSeq: " + str(self.rtspSeq) + "\n"
			request += "Session: " + str(self.sessionId)
			self.requestSent = self.PLAY
		
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = "PAUSE " + self.fileName + " RTSP/1.0\n"
			request += "CSeq: " + str(self.rtspSeq) + "\n"
			request += "Session: " + str(self.sessionId)
			self.requestSent = self.PAUSE
			
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = "TEARDOWN " + self.fileName + " RTSP/1.0\n"
			request += "CSeq: " + str(self.rtspSeq) + "\n"
			request += "Session: " + str(self.sessionId)
			self.requestSent = self.TEARDOWN
		else:
			return
		
		self.rtspSocket.send(request.encode())
	
	def recvRtspReply(self):
		while True:
			if self.exitEvent.is_set(): break
			try:
				reply = self.rtspSocket.recv(1024)
				if reply: 
					self.parseRtspReply(reply.decode("utf-8"))
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					break
			except: break
	
	def parseRtspReply(self, data):
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			if self.sessionId == 0: self.sessionId = session
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200: 
					if self.requestSent == self.SETUP:
						print("Transition: INIT -> READY") # LOG CHUYỂN TRẠNG THÁI
						self.state = self.READY
						self.openRtpPort() 
					elif self.requestSent == self.PLAY:
						print("Transition: READY -> PLAYING") # Chuyển tt
						self.state = self.PLAYING
						if not hasattr(self, 'rtp_thread'):
							self.rtp_thread = threading.Thread(target=self.receiveRtp)
							self.rtp_thread.start()
							self.display_thread = threading.Thread(target=self.consumeBuffer)
							self.display_thread.start()
							self.isBuffering = True
					elif self.requestSent == self.PAUSE:
						print("Transition: PLAYING -> READY") # Chuyển tt
						self.state = self.READY
						self.playEvent.set()
					elif self.requestSent == self.TEARDOWN:
						print("Transition: -> INIT") # Chuyển tt
						self.state = self.INIT
						self.teardownAcked = 1 
	
	def openRtpPort(self):
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.5)
		try:
			self.rtpSocket.bind(('', self.rtpPort))
		except:
			tkinter.messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		self.pauseMovie()
		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else:
			self.playMovie()