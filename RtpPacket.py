import sys
from time import time
HEADER_SIZE = 12
MAX_PAYLOAD_SIZE = 1400 # Maximum payload size for fragmentation

class RtpPacket:	
    header = bytearray(HEADER_SIZE)
    
    def __init__(self):
        pass
        
    def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
        """Encode the RTP packet with header fields and payload."""
        timestamp = int(time())
        header = bytearray(HEADER_SIZE)
        
        # Byte 0: V(2), P(1), X(1), CC(4)
        header[0] = (version << 6) | (padding << 5) | (extension << 4) | cc
        
        # Byte 1: M(1), PT(7)
        header[1] = (marker << 7) | pt
        
        # Bytes 2-3: Sequence Number (16 bits)
        header[2] = (seqnum >> 8) & 0xFF
        header[3] = seqnum & 0xFF
        
        # Bytes 4-7: Timestamp (32 bits)
        header[4] = (timestamp >> 24) & 0xFF
        header[5] = (timestamp >> 16) & 0xFF
        header[6] = (timestamp >> 8) & 0xFF
        header[7] = timestamp & 0xFF
        
        # Bytes 8-11: SSRC (32 bits)
        header[8] = (ssrc >> 24) & 0xFF
        header[9] = (ssrc >> 16) & 0xFF
        header[10] = (ssrc >> 8) & 0xFF
        header[11] = ssrc & 0xFF
        
        # Store header
        self.header = header
        
        # Get the payload from the argument
        self.payload = payload
    
    # Fragment large frames
    @staticmethod
    def fragmentFrame(frameData, frameNbr, version=2, padding=0, extension=0, cc=0, pt=26, ssrc=0):
        """
        Fragment a large frame into multiple RTP packets.
        Returns: List of RTP packets
        """
        fragments = []
        totalSize = len(frameData)
        offset = 0
        fragmentIndex = 0
        
        while offset < totalSize:
            # Calculate size of this fragment
            remainingSize = totalSize - offset
            fragmentSize = min(MAX_PAYLOAD_SIZE, remainingSize)
            
            # Get fragment data
            fragmentData = frameData[offset:offset + fragmentSize]
            
            # Determine marker bit
            isLastFragment = (offset + fragmentSize >= totalSize)
            marker = 1 if isLastFragment else 0
            
            # Create RTP packet
            # Sequence number = frameNbr * 100 + fragmentIndex (để phân biệt fragments)
            seqnum = frameNbr * 100 + fragmentIndex
            
            rtpPacket = RtpPacket()
            rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, fragmentData)
            
            fragments.append(rtpPacket.getPacket())
            
            offset += fragmentSize
            fragmentIndex += 1
            
        print(f"Frame {frameNbr}: Divide {len(fragments)} fragments (Total size: {totalSize} bytes)")
        return fragments
        
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
    
    def marker(self):
        """Return marker bit ."""
        return int(self.header[1] >> 7)
    
    def getPayload(self):
        """Return payload."""
        return self.payload
        
    def getPacket(self):
        """Return RTP packet."""
        return self.header + self.payload
