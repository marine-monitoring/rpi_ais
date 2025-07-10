#******************
# NMEA Logger
# Main component of the NMEA Logger application
# Developed with Python 3.7.3
# Requires nmea_logging.config
#******************
# Command line parameters:
# None:                       nmea_logger.py
# Without FTP transfer:       nmea_logger.py --notransfer
# Delete file after transfer: nmea_logger.py --deleteaftertransfer
#******************


import threading
import time
import serial
import string
import serial.tools.list_ports
import glob
import sys
import RPi.GPIO as GPIO
import os
import psutil
import logging
import shutil
import zipfile
import math
import ftplib
import argparse
import subprocess
import socket
from datetime import datetime
from nmea_clock import check_clock
from configparser import ConfigParser
from pathlib import Path


time_to_exit = False
current_location = (0,0)
transfer_on = False
#Counts the threads that have been started and need to be closed
#before the program can be user-terminated. This is primarily necessary to ensure
#the script is not terminated during an FTP file transfer.
threads_to_close = 0

logging.basicConfig(filename="/home/pi/nmea_logger/nmea_logging.log", level=logging.INFO, format='%(asctime)s %(message)s' )
logging.info("\n")
logging.info("*** Program start")

def main():
   global threads_to_close
   
   #Redirect STDOUT to logging
   stdout_logger = logging.getLogger('STDOUT')
   sl = StreamToLogger(stdout_logger, logging.INFO)
   sys.stdout = sl

   #Redirect STDERR to logging
   stderr_logger = logging.getLogger('STDERR')
   sl = StreamToLogger(stderr_logger, logging.ERROR)
   sys.stderr = sl

   GPIO.setmode(GPIO.BCM)
   GPIO.setwarnings(False)
   #RED Error LED
   GPIO.setup(21,GPIO.OUT)
   #BLUE LED
   GPIO.setup(20,GPIO.OUT)
   #GREEN LED
   GPIO.setup(26,GPIO.OUT)
   #OFF BUTTON
   GPIO.setup(13,GPIO.IN,pull_up_down=GPIO.PUD_UP)

   time.sleep(10)

   #Check valid time available
   rvc = check_clock() 
   if "Error" in rvc:
      logging.info(rvc)
      #Blink clock error code 5 times then quit
      for c in range(5):
         for cc in range(4):
            GPIO.output(21,GPIO.HIGH)
            time.sleep(0.2)
            GPIO.output(21,GPIO.LOW)
            time.sleep(0.2)
            GPIO.output(21,GPIO.LOW)
            time.sleep(0.2)
         time.sleep(2)
      sys.exit() 

   res_chk = media_path()
   if res_chk == "no_writable_media" or res_chk == "":
      logging.info("No writable media")
      for c in range(5):
         GPIO.output(21,GPIO.HIGH)
         time.sleep(0.2)
         GPIO.output(21,GPIO.LOW)
         time.sleep(0.2)
         GPIO.output(21,GPIO.HIGH)
         time.sleep(0.2)
         GPIO.output(21,GPIO.LOW)
         time.sleep(2)
   else:
      cmedia = res_chk
      
   logging.info("Writing files to " + cmedia)
      
   parser = ConfigParser()
   parser.read('/home/pi/nmea_logger/nmea_logging.config')
   
   data_source  = parser.get('General', 'data_source')
   outfilesiz  = int(parser.get('General', 'output_file_size'))
   outfileext  = parser.get('General', 'output_file_name_extension')
   vessel_name = parser.get('General', 'vessel')
   transfer_enabled = int(parser.get('General', 'ftp_transfer_enabled'))
   delete_after_transfer = int(parser.get('General', 'delete_after_transfer'))
   save_all_nmea = int(parser.get('General', 'save_all_nmea'))
   nmea_sentence_types = parser.get('General', 'nmea_sentence_types')
   nmea_sentence_types = nmea_sentence_types.split(",")
   ftp_server = parser.get('General', 'ftp_server')
   ftp_user = parser.get('General', 'ftp_user')
   ftp_password = parser.get('General', 'ftp_password')
   ftp_wait_sec = int(parser.get('General', 'ftp_wait_sec'))
   ftp_use_ports_file = int(parser.get('General', 'ftp_use_ports_file'))
   tcp_sourceip = parser.get('tcp', 'tcp_sourceip')
   tcp_port = int(parser.get('tcp', 'tcp_port'))
   
   #Before starting processing move any stray data
   #files that may be left in the media dir to the media/complete dir and zip
   #them there. Stray files may be produced when the program crashes or shutdown
   #did not complete orderly. Cannot cleanup these files when the logging is running
   #since the current, open dat files that are being logged to would also be moved.
   flashdrive = "/media/pi/" + cmedia + "/"
   
   try:
      for root, dirs, files in os.walk(flashdrive):
         for filename in files:
            if filename.endswith("." + outfileext):
               filename_woext = filename.replace("." + outfileext, "")
               shutil.copyfile(flashdrive + filename, flashdrive + "complete/" + filename)
               #if copy was successful and copied file exists, delete it
               if os.path.isfile(flashdrive + "complete/" + filename):
                  if os.path.isfile(flashdrive + filename):
                     os.remove(flashdrive + filename)
                  #zip copied file
                  zipObj = zipfile.ZipFile(flashdrive + "complete/" + filename_woext + "." + "zip", 'w')
                  zipObj.write(flashdrive + "complete/" + filename, compress_type=zipfile.ZIP_DEFLATED, arcname=filename)
                  zipObj.close()
                  #delete file if zip was succcessful
               if os.path.isfile(flashdrive + "complete/" + filename):
                  os.remove(flashdrive + "complete/" + filename)
   except:
       pass

   if data_source == "com":
      ports = ['ttyUSB0','ttyUSB1','ttyUSB2']
      for po in ports:
         name      = parser.get(po, 'name')
         port      = parser.get(po, 'port')
         baud_rate = int(parser.get(po, 'baud_rate'))
         data_bits = int(parser.get(po, 'data_bits'))
         parity    = parser.get(po, 'parity')
         stop_bits = int(parser.get(po, 'stop_bits'))
         timeout   = int(parser.get(po, 'timeout'))
         if po == 'ttyUSB0':
            led = 20
         if po == 'ttyUSB1':
            led = 20
         if po == 'ttyUSB2':
            led = 20

         #check that USB port exists
         pd = [] 
         pd = os.listdir("/dev")
         if po in pd:
            thc = threading.Thread(target=th_log_serial,args=(name,port,baud_rate,data_bits,parity,stop_bits,timeout,led,outfilesiz,outfileext,cmedia,save_all_nmea,nmea_sentence_types))
            thc.start()
            threads_to_close = threads_to_close + 1

   if data_source == "tcp":
      name = "tcp"
      led = 20
      tht = threading.Thread(target=th_log_tcp2,args=(name,tcp_sourceip,tcp_port,led,outfilesiz,outfileext,cmedia,save_all_nmea,nmea_sentence_types))
      tht.start()
      threads_to_close = threads_to_close + 1

   #Monitoring thread
   the = threading.Thread(target=th_mon,args=(cmedia,))
   the.start()
   threads_to_close = threads_to_close + 1
   
   #Thread for stop detection
   ths = threading.Thread(target=th_stop,args=())
   ths.start()
   threads_to_close = threads_to_close + 1

   #Thread for transferring data
   if transfer_enabled == 1:
      tht = threading.Thread(target=th_transfer,args=(cmedia,vessel_name,delete_after_transfer,ftp_server,ftp_user,ftp_password,ftp_wait_sec,ftp_use_ports_file))
      tht.start()
      threads_to_close = threads_to_close + 1

   logging.info("End")

#Includes reconnect after fail  
def th_log_tcp2(name,tcp_sourceip,tcp_port,led,outfilesize,outfileext,media,save_all_nmea,nmea_sentence_types):
   global current_location
   global threads_to_close

   time.sleep(3)
   TEN_MINUTES = 10 * 60 * 1000

   logging.info("Thread runing to log from TCP")
   logging.info("Using media " + media)

   while media == "":
      time.sleep(2)
   #Use first listed entry
   flashdrive = "/media/pi/" + media + "/"

   timestr = time.strftime("%Y%m%d-%H%M%S")
   outfile = open(flashdrive + timestr + "-" + name + "." + outfileext, "a+", 1)

   clientSocket = socket.socket()

   clientSocket.connect((tcp_sourceip, tcp_port))
    # keep track of connection status  
   connected = True  
   logging.info("TCP: connected with " + tcp_sourceip + " " + str(tcp_port))

   bytectr = 0
   last_pos_time = 0
   last_radar_time = 0
   ser_err_amt = 0
   capt_pos = 0 

   tr = b""
   while True:
      try:
         rec = clientSocket.recv(2048)
         #This send is crucial. Without it some NMEA TCP sockets will fail, eventually
         clientSocket.send( bytes("csiro_nmea_logger", "UTF-8"))  
         tr = tr + rec
         if len(rec) < 2048:
            cf = tr.decode('utf8', 'ignore')
            re = cf.split("\r\n")
            for outdec in re:
               if len(outdec) > 0:
                  dtstmp = datetime.utcnow().strftime("%Y%m%d-%H%M%S.%f")[:-3] + " UTC,"
                  if save_all_nmea == 1:
                     bytectr = bytectr + len(dtstmp) + len(outdec)
                     outfile.write(dtstmp + outdec + "\n")
                  else:
                     sen_in = any(nst in outdec for nst in nmea_sentence_types)
                     if sen_in:
                        bytectr = bytectr + len(dtstmp) + len(outdec)
                        outfile.write(dtstmp + outdec + "\n")
            tr = b""
            time.sleep(5)

         #Blink green if pos and radar sentences have been received in the last 10 minutes, blink blue if not
         if "GGA" in outdec:
            #Capture position for determing if ftp file transfer can take place. Only every 100ths record.
            if capt_pos == 100:
               last_pos_time = int(round(time.time() * 1000))
               capt_pos = 0
            try:
               lpt = outdec.split("GGA,")[1]
               lpt_lat = float(lpt.split(",")[1])/100
               if lpt.split(",")[2] == 'S':
                  lpt_lat = lpt_lat * -1
               tup1 = (math.modf(lpt_lat)[1], math.modf(lpt_lat)[0] * 100, 0)
               lpt_lat = dms2dd(tup1)
               lpt_lon = float(lpt.split(",")[3])/100
               if lpt.split(",")[4] == 'W':
                  lpt_lon = lpt_lon * -1
               tup1 = (math.modf(lpt_lon)[1], math.modf(lpt_lon)[0] * 100, 0)
               lpt_lon = dms2dd(tup1)
               current_location = (lpt_lat, lpt_lon)
            except:
               current_location = (0,0)
               capt_pos = 0
            capt_pos = capt_pos + 1
         
         if "TTM" in outdec:
            last_radar_time = int(round(time.time() * 1000))

         tenMinAgo = int(round(time.time() * 1000)) - TEN_MINUTES

         if last_pos_time > tenMinAgo and last_radar_time > tenMinAgo:
            GPIO.output(26,GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(26,GPIO.LOW)
         else:
            GPIO.output(20,GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(20,GPIO.LOW)

         if bytectr > outfilesize:
            if not os.path.exists(flashdrive + "complete"):
               os.mkdir(flashdrive + "complete")
            outfile.close()
            logging.info("Done writing to file " + flashdrive + timestr + "-" + name + "." + outfileext)
            #copy file to complete dir
            shutil.copyfile(flashdrive + timestr + "-" + name + "." + outfileext, flashdrive + "complete/" + timestr + "-" + name + "." + outfileext)
            #if copy was successful and copied file exists, delete it
            if os.path.isfile(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext):
               if os.path.isfile(flashdrive + timestr + "-" + name + "." + outfileext):
                  os.remove(flashdrive + timestr + "-" + name + "." + outfileext)
               #zip copied file
               zipObj = zipfile.ZipFile(flashdrive + "complete/" + timestr + "-" + name + "." + "zip", 'w')
               an = timestr + "-" + name + "." + outfileext
               zipObj.write(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext, compress_type=zipfile.ZIP_DEFLATED, arcname=an)
               zipObj.close()
               logging.info("File zipped")
               #delete file if zip was succcessful
            if os.path.isfile(flashdrive + "complete/" + timestr + "-" + name + "." + "zip"):
               os.remove(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext)
            timestr = time.strftime("%Y%m%d-%H%M%S")
            outfile = open(flashdrive + timestr + "-" + name + "." + outfileext, "a+", 1)
            bytectr = 0

         if time_to_exit:
            logging.info(str(ser_err_amt) + " serial errors from port " + port)
            logging.info("Exit from " + port)
            threads_to_close = threads_to_close - 1
            return

      except (socket.error, socket.timeout):
         connected = False  
         clientSocket = socket.socket()  
         logging.info("TCP: connection lost. Attempting to reconnect")
         while not connected:  
            try:  
               clientSocket.connect((tcp_sourceip, tcp_port))
               connected = True  
               logging.info("TCP: Reconnection successful")
            except socket.error:  
               time.sleep( 2 )  
   clientSocket.close();

def th_log_serial(name,port,baud_rate,data_bits,parity,stop_bits,timeout,led,outfilesize,outfileext,media,save_all_nmea,nmea_sentence_types):
   global current_location
   global threads_to_close

   time.sleep(3)
   TEN_MINUTES = 10 * 60 * 1000
   
   with serial.Serial(port=port,baudrate=baud_rate,bytesize=data_bits,parity=parity,stopbits=stop_bits,timeout=timeout) as ser:
      logging.info("Thread runing to log from " + port)
      logging.info("Using media " + media)

      while media == "":
         time.sleep(2)
      #Use first listed entry
      flashdrive = "/media/pi/" + media + "/"

      timestr = time.strftime("%Y%m%d-%H%M%S")
      outfile = open(flashdrive + timestr + "-" + name + "." + outfileext, "a+", 1)
      ser.flushInput()
      
      bytectr = 0
      last_pos_time = 0
      last_radar_time = 0
      ser_err_amt = 0
      capt_pos = 0 
      
      while True:
         try:
            output = ser.readline()
            outdec = output.decode("utf-8")
            dtstmp = datetime.utcnow().strftime("%Y%m%d-%H%M%S.%f")[:-3] + " UTC,"
            if save_all_nmea == 1:
               bytectr = bytectr + len(dtstmp) + len(outdec)
               outfile.write(dtstmp + outdec)
            else:
               sen_in = any(nst in outdec for nst in nmea_sentence_types)
               if sen_in:
                  bytectr = bytectr + len(dtstmp) + len(outdec)
                  outfile.write(dtstmp + outdec)
         except:
            outdec = ""
            ser_err_amt = ser_err_amt + 1
            if ser_err_amt % 100 == 0:
               logging.info("100 serial errors from port " + port)
         
         #Blink green if pos and radar sentences have been received in the last 10 minutes,
         #blink blue if not
         if "GGA" in outdec:
            #Capture position for determing if ftp file transfer can take place
            #only every 100ths record.
            if capt_pos == 100:
               last_pos_time = int(round(time.time() * 1000))
               capt_pos = 0
            try:
               lpt = outdec.split("GGA,")[1]
               lpt_lat = float(lpt.split(",")[1])/100
               if lpt.split(",")[2] == 'S':
                  lpt_lat = lpt_lat * -1
               tup1 = (math.modf(lpt_lat)[1], math.modf(lpt_lat)[0] * 100, 0)
               lpt_lat = dms2dd(tup1)
               lpt_lon = float(lpt.split(",")[3])/100
               if lpt.split(",")[4] == 'W':
                  lpt_lon = lpt_lon * -1
               tup1 = (math.modf(lpt_lon)[1], math.modf(lpt_lon)[0] * 100, 0)
               lpt_lon = dms2dd(tup1)
               current_location = (lpt_lat, lpt_lon)
            except:
               current_location = (0,0)
               capt_pos = 0
            capt_pos = capt_pos + 1
         
         if "TTM" in outdec:
            last_radar_time = int(round(time.time() * 1000))

         tenMinAgo = int(round(time.time() * 1000)) - TEN_MINUTES

         if last_pos_time > tenMinAgo and last_radar_time > tenMinAgo:
            GPIO.output(26,GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(26,GPIO.LOW)
         else:
            GPIO.output(20,GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(20,GPIO.LOW)

         if bytectr > outfilesize:
            if not os.path.exists(flashdrive + "complete"):
               os.mkdir(flashdrive + "complete")
            outfile.close()
            logging.info("Done writing to file " + flashdrive + timestr + "-" + name + "." + outfileext)
            #copy file to complete dir
            shutil.copyfile(flashdrive + timestr + "-" + name + "." + outfileext, flashdrive + "complete/" + timestr + "-" + name + "." + outfileext)
            #if copy was successful and copied file exists, delete it
            if os.path.isfile(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext):
               if os.path.isfile(flashdrive + timestr + "-" + name + "." + outfileext):
                  os.remove(flashdrive + timestr + "-" + name + "." + outfileext)
               #zip copied file
               zipObj = zipfile.ZipFile(flashdrive + "complete/" + timestr + "-" + name + "." + "zip", 'w')
               an = timestr + "-" + name + "." + outfileext
               zipObj.write(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext, compress_type=zipfile.ZIP_DEFLATED, arcname=an)
               zipObj.close()
               logging.info("File zipped")
               #delete file if zip was succcessful
            if os.path.isfile(flashdrive + "complete/" + timestr + "-" + name + "." + "zip"):
               os.remove(flashdrive + "complete/" + timestr + "-" + name + "." + outfileext)
            timestr = time.strftime("%Y%m%d-%H%M%S")
            outfile = open(flashdrive + timestr + "-" + name + "." + outfileext, "a+", 1)
            bytectr = 0

         if time_to_exit:
            logging.info(str(ser_err_amt) + " serial errors from port " + port)
            logging.info("Exit from " + port)
            threads_to_close = threads_to_close - 1
            return

def th_mon(media):
   global threads_to_close
   # **************************************
   # ERROR CODES:
   # two short blinks - disk 90% full
   # **************************************
   logging.info("Monitor thread started")
   #Test LEDs
   led_list = [21,20,26]
   for l in led_list:
      GPIO.output(l,GPIO.HIGH)
      time.sleep(0.3)
      GPIO.output(l,GPIO.LOW)
      time.sleep(0.1)
      GPIO.output(l,GPIO.HIGH)
      time.sleep(0.3)
      GPIO.output(l,GPIO.LOW)
      time.sleep(0.2)

   time.sleep(1)

   while not time_to_exit:
      flashdrive = "/media/pi/" + media + "/"
      hdd = psutil.disk_usage(flashdrive)
      percent_free = hdd.free / hdd.total
      if percent_free < 0.1:
         logging.info("90% flash drive usage reached")
         GPIO.output(21,GPIO.HIGH)
         time.sleep(0.2)
         GPIO.output(21,GPIO.LOW)
         time.sleep(0.2)
         GPIO.output(21,GPIO.HIGH)
         time.sleep(0.2)
         GPIO.output(21,GPIO.LOW)
      time.sleep(2)
   threads_to_close = threads_to_close - 1

def th_stop():
   global threads_to_close
   logging.info("Stop thread started")
   off_pressed = 0
   while True:
      if GPIO.input(13) == GPIO.LOW:
         off_pressed = off_pressed + 1
         if off_pressed == 5: #OFF pressed for 5 seconds
            logging.info("OFF pressed")
            global time_to_exit
            time_to_exit = True
            threads_to_close = threads_to_close - 1
            while threads_to_close > 0:
               pass

            #Blink green, blue red when ending program 
            GPIO.output(26,GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(26,GPIO.LOW)
            GPIO.output(20,GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(20,GPIO.LOW)
            GPIO.output(21,GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(21,GPIO.LOW)
                  
            #time.sleep(3)
            #subprocess.call(["sudo shutdown", "-h", "now"])
            #os.system("shutdown now -h")
      if GPIO.input(13) == GPIO.HIGH:
         off_pressed = 0

      time.sleep(1)
   
def th_transfer(media,vessel_name,delete_after_transfer,ftp_server,ftp_user,ftp_password,ftp_wait_sec,ftp_use_ports_file):
   global threads_to_close
   global current_location

   logging.info("Transfer thread started")
   cl = current_location
   portlist = []
   can_transmit = False
   flashdrive = "/media/pi/" + media + "/"

   if ftp_use_ports_file == 1:
      #Read ports (where data transfer can take place) into list for later use
      ports = open('/home/pi/nmea_logger/ports_v1.txt', 'r')
      pl = ports.readlines()
      for line in pl:
         if line != "\n" and line[0] != "#" and line != "[ports]\n": 
            elem = line.split(" ")
            location = elem[0].strip()

            top_left = elem[1].strip().replace("(","")
            top_left = top_left.replace(")","")
            tl = top_left.split(",")
            tla = float(tl[0])
            tlo = float(tl[1])

            bottom_right = elem[2].strip().replace(")","")
            bottom_right = bottom_right.replace("(","")
            br = bottom_right.split(",")
            bla = float(br[0])
            blo = float(br[1])
            
            portentry = [location,tla,tlo,bla,blo]
            portlist.append(portentry)
   else:
      can_transmit = True
       
   while not time_to_exit:
      if ftp_use_ports_file == 1:
         cl = current_location
         for ple in portlist:
            if cl[0] < ple[1] and cl[0] > ple[3] and cl[1] > ple[2] and cl[1] < ple[4]:
               can_transmit = True
               break
            else:
               can_transmit = False

      if can_transmit:
         files_to_transfer = []
         for root, dirs, files in os.walk(flashdrive + "complete/"):
            for filename in files:
               if filename.endswith(".zip"):
                  files_to_transfer.append(filename)
         if files_to_transfer:
            try:
               session = ftplib.FTP(ftp_server, ftp_user, ftp_password, timeout=20)
               session.set_debuglevel(1)
               session.cwd("/" + vessel_name)
               for ftt in files_to_transfer:
                  file = open(flashdrive + "complete/" + ftt,'rb')
                  session.storbinary('STOR ' + ftt, file)
                  file.close()
                  #Delete local file if transfer was successful and file size equal
                  if ftt in session.nlst():
                     remote_file_size = session.size(ftt)
                     local_file_size = Path(flashdrive + "complete/" + ftt).stat().st_size
                     if remote_file_size == local_file_size:
                        logging.info("File " + ftt + " sucessfully transferred")
                        if delete_after_transfer == 1:
                           if os.path.exists(flashdrive + "complete/" + ftt):
                              os.remove(flashdrive + "complete/" + ftt)
                              logging.info("local file  " + ftt + " deleted")
                        else:
                        #Move file to transferred dir
                           shutil.move(flashdrive + "complete/" + ftt, flashdrive + "transferred/" + ftt)
                           logging.info("file " + ftt + " moved to transferred dir")
               session.quit()
            except ftplib.all_errors as e:
               try:
                  logging.info("FTP error: " + str(e))
                  session.close()
               except:
                  pass
            finally:
               try:
                  logging.info("FTP error")
                  session.close()
               except:
                  pass

      time.sleep(ftp_wait_sec)
   threads_to_close = threads_to_close - 1

def media_path():
   ld = []
   ld = os.listdir("/media/pi")
   for en in ld:
      ofil = "/media/pi/" + en + "/testnmeaout.txt"
      try:
         tfil = open(ofil,"w")
         tfil.write("test")
         tfil.close()
         f = open(ofil)
         rd = f.read()
         if rd == "test":
            f.close()
            os.remove(ofil)
            #Check that complete dir exists. If not create it
            if not os.path.exists("/media/pi/" + en + "/complete"):
                os.makedirs("/media/pi/" + en + "/complete")
            #Check that transferred dir exists. If not create it
            if not os.path.exists("/media/pi/" + en + "/transferred"):
                os.makedirs("/media/pi/" + en + "/transferred")
            return en
      except:
         logging.info(en + " is not writable")
   logging.info("No_writeable_media")
   return "no_writeable_media"

def dms2dd(tup1):
   dd = float(tup1[0]) + float(tup1[1])/60 + float(tup1[2])/(60*60)
   return dd

class StreamToLogger(object):
      #Fake file-like stream object that redirects writes to a logger instance.
      def __init__(self, logger, log_level=logging.INFO):
            self.logger = logger
            self.log_level = log_level
            self.linebuf = ''

      def write(self, buf):
            for line in buf.rstrip().splitlines():
                  self.logger.log(self.log_level, line.rstrip())

logging.basicConfig(
level=logging.DEBUG,
format='%(asctime)s:%(levelname)s:%(name)s:%(message)s',
filename="out.log",
filemode='a'
)

if __name__ == "__main__":
   main()
