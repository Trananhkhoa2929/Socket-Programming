import sys
from tkinter import Tk
from Client import Client

if __name__ == "__main__":
	try:
		serverAddr = sys.argv[1]
		serverPort = sys.argv[2]
		rtpPort = sys.argv[3]
		fileName = sys.argv[4]	
	except:
		print("[Usage: ClientLauncher.py Server_name Server_port RTP_port Video_file]\n")	
		# Default values for easier testing if arguments not provided
		# You can remove this or modify as needed
		# serverAddr = "127.0.0.1"
		# serverPort = "1025"
		# rtpPort = "5000"
		# fileName = "movie.Mjpeg"
		sys.exit()

	root = Tk()
	
	# Create a new client
	app = Client(root, serverAddr, serverPort, rtpPort, fileName)
	app.master.title("RTPClient")	
	root.mainloop()