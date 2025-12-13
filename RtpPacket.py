import sys
from time import time
HEADER_SIZE = 12

class RtpPacket:
	header = bytearray(HEADER_SIZE)

	def __init__(self):
		pass

	def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
		"""Encode the RTP packet with header fields and payload."""
		timestamp = int(time())
		header = bytearray(HEADER_SIZE)
		#--------------
		# TO COMPLETE
		#--------------
		# Fill the header bytearray with RTP header fields

		# Header[0] = Version(2) | Padding(1) | Extension(1) | CC(4)
		header[0] = (version << 6) & 0xC0  # 2 bits Version
		header[0] = header[0] | ((padding << 5) & 0x20) # 1 bit Padding
		header[0] = header[0] | ((extension << 4) & 0x10) # 1 bit Extension
		header[0] = header[0] | (cc & 0x0F) # 4 bits CC

		# Header[1] = Marker(1) | PayloadType(7)
		header[1] = (marker << 7) & 0x80 # 1 bit Marker
		header[1] = header[1] | (pt & 0x7F) # 7 bits Payload Type

		# Header[2-3] = Sequence Number
		header[2] = (seqnum >> 8) & 0xFF
		header[3] = seqnum & 0xFF

		# Header[4-7] = Timestamp
		header[4] = (timestamp >> 24) & 0xFF
		header[5] = (timestamp >> 16) & 0xFF
		header[6] = (timestamp >> 8) & 0xFF
		header[7] = timestamp & 0xFF

		# Header[8-11] = SSRC
		header[8] = (ssrc >> 24) & 0xFF
		header[9] = (ssrc >> 16) & 0xFF
		header[10] = (ssrc >> 8) & 0xFF
		header[11] = ssrc & 0xFF

		self.header = header
		self.payload = payload

	def decode(self, byteStream):
		"""Decode the RTP packet."""
		self.header = bytearray(byteStream[:HEADER_SIZE])
		self.payload = byteStream[HEADER_SIZE:]

	def version(self):
		"""Return RTP version."""
		return int(self.header[0] >> 6)

	def seqNum(self):
		"""Return sequence (frame) number."""
		seqNum = self.header[2] << 8 | self.header[3]
		return int(seqNum)

	def timestamp(self):
		"""Return timestamp."""
		timestamp = self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7]
		return int(timestamp)

	def payloadType(self):
		"""Return payload type."""
		pt = self.header[1] & 127
		return int(pt)

	def getPayload(self):
		"""Return payload."""
		return self.payload

	def getPacket(self):
		"""Return RTP packet."""
		return self.header + self.payload

	def marker(self):
		"""Return marker bit."""
		return (self.header[1] >> 7) & 1