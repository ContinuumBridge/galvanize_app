#!/usr/bin/env python
# spur_app_a.py
"""
Copyright (c) 2015 ContinuumBridge Limited

Allocation of addresses:
Byte 1 MSB: 1=bridge, 0=node
Byte 1[6:0]: the 7 LSBs of the bridge ID that a node first connected to
Byte 0: allocated by bridge that node first connected to

"""

import sys
#reload(sys)
#sys.setdefaultencoding('utf-8')
import time
import json
import pickle
import struct
import base64
import random
from cbcommslib import CbApp, CbClient
from cbconfig import *
from twisted.internet import reactor

FUNCTIONS = {
    "include_req": 0x00,
    "s_include_req": 0x01,
    "include_grant": 0x02,
    "reinclude": 0x04,
    "config": 0x05,
    "send_battery": 0x06,
    "alert": 0x09,
    "woken_up": 0x07,
    "ack": 0x08,
    "beacon": 0x0A,
    "start": 0x0B,
    "nack": 0x0C,
    "include_not": 0x0D,
    "configuring": 0x0E,
    "reset": 0xFF
}
Y_STARTS = (
    (38, 0, 0 ,0, 0),
    (18, 56, 0, 0, 0),
    (4, 34, 64, 0, 0),
    (4, 26, 48, 70, 0),
    (0, 20, 40, 60, 80)
);

SPUR_ADDRESS        = int(CB_BID[3:])
CHECK_INTERVAL      = 1800
TIME_TO_FIRST_CHECK = 60               # Time from start to sending first status message
CID                 = "CID157"         # Client ID Staging
#CID                 = "CID249"          # Client ID Production
GRANT_ADDRESS       = 0xBB00
PRESSED_WAKEUP      = 5*60              # How long node should sleep for in pressed state, seconds/2
BEACON_START_DELAY  = 5                 # Delay before starting to send beacons to allow other things to start
GRACE_TIME_MULT     = 1.2               # Time to wait after we should have heard from node before reporting it missing
MONITOR_INTERVAL    = 10                # Check to see if nodes are overdue in waking up at this interval
config              = {
                        "nodes": [ ]
}
DEFAULT_WAKEUP_INTERVAL = 21600        # How often a node wakes up = 6 hours

class App(CbApp):
    def __init__(self, argv):
        self.appClass           = "control"
        self.state              = "stopped"
        self.id2addr            = {}          # Node id to node address mapping
        self.id2addr[0]         = 0           # For including
        self.addr2id            = {}          # Node address to node if mapping
        self.addr2id[0]         = 0
        self.radioOn            = True
        self.messageQueue       = []
        self.sentTo             = []
        self.includeGrants      = []
        self.nodeConfig         = {} 
        self.wakeups            = {}
        self.wakeupCount        = {}
        self.beaconCalled       = 0
        self.including          = []
        self.configuring        = []
        self.sendingConfig      = []
        self.buttonState        = {}
        self.requestBatteries   = []
        self.nextWakeupTime     = {}
        self.beaconInterval     = 6
        self.beaconRunning      = False
        self.findingRssiAddr        = None
        #self.testCount         = 0           # Test use only
        #self.ackCount          = 0           # Used purely for test of nack

        # Super-class init must be called
        CbApp.__init__(self, argv)

    def setState(self, action):
        self.state = action
        msg = {"id": self.id,
               "status": "state",
               "state": self.state}
        self.sendManagerMessage(msg)

    def save(self):
        state = {
            "id2addr": self.id2addr,
            "addr2id": self.addr2id,
            "buttonState": self.buttonState,
            "wakeupCount": self.wakeupCount,
            "wakeups": self.wakeups
        }
        try:
            with open(self.saveFile, 'w') as f:
                pickle.dump(state, f)
                #self.cbLog("debug", "saving state: " + str(json.dumps(state, indent=4)))
                self.cbLog("debug", "saving state: " + str(state))
        except Exception as ex:
            self.cbLog("warning", "Problem saving state. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def loadSaved(self):
        try:
            if os.path.isfile(self.saveFile):
                with open(self.saveFile, 'r') as f:
                    state = pickle.load(f)
                self.cbLog("debug", "Loaded saved state: " + str(json.dumps(state, indent=4)))
                self.id2addr = state["id2addr"]
                self.addr2id = state["addr2id"]
                self.buttonState = state["buttonState"]
                self.wakeupCount = state["wakeupCount"]
                self.wakeups = state["wakeups"]
        except Exception as ex:
            self.cbLog("warning", "Problem loading saved state. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def onStop(self):
        self.save()

    def reportRSSI(self, rssi):
        msg = {"id": self.id,
               "status": "user_message",
               "body": "LPRS RSSI: " + str(rssi)
              }
        self.sendManagerMessage(msg)

    def checkConnected(self):
        toClient = {"status": "init"}
        self.client.send(toClient)
        reactor.callLater(CHECK_INTERVAL, self.checkConnected)

    def onConcMessage(self, message):
        self.client.receive(message)

    def onClientMessage(self, message):
        if True:
        #try:
            self.cbLog("debug", "onClientMessage, message: " + str(json.dumps(message, indent=4)))
            if "function" in message:
                if message["function"] == "include_grant":
                    nodeID = int(message["id"])
                    addr = int(message["address"])
                    self.cbLog("debug", "onClientMessage, include_grant. nodeID: {}, addr: {}".format(nodeID, addr))
                    if nodeID not in self.id2addr:
                        self.id2addr[nodeID] = addr
                        self.addr2id[addr] = nodeID
                        self.save()
                    data = struct.pack(">IH", nodeID, self.id2addr[nodeID])
                    msg = self.formatRadioMessage(GRANT_ADDRESS, "include_grant", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
                    # If everything happens too quickly, a button may not be ready for include_grant, so add a delay
                    reactor.callLater(0.5, self.queueRadio, msg, self.id2addr[nodeID], "include_grant")
                    self.cbLog("debug", "onClientMessage, adding {} to includeGrants".format(self.id2addr[nodeID]))
                    self.includeGrants.append(self.id2addr[nodeID])
                    if self.id2addr[nodeID] in self.requestBatteries:
                        self.requestBatteries.remove(self.id2addr[nodeID])
                elif message["function"] == "include_not":
                    nodeID = int(message["id"])
                    data = struct.pack(">I", nodeID)
                    msg = self.formatRadioMessage(GRANT_ADDRESS, "include_not", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
                    self.queueRadio(msg, 0x00, "include_not")
                    reactor.callLater(2, self.queueRadio, msg, 0x00, "include_not")
                elif message["function"] == "config":
                    self.cbLog("debug", "onClientMessage, message[node]: " + str(message["id"]))
                    nodeID = int(message["id"])
                    nodeAddr = self.id2addr[int(message["id"])]
                    if "name" in message["config"]:  # Update everything, so remove any config that's already waiting
                        self.cbLog("debug", "onClientMessage, complete new config for: {}".format(nodeAddr))
                        self.nodeConfig[nodeAddr] = message["config"]
                    elif nodeAddr in self.nodeConfig:  # We already have some partial config
                        self.cbLog("debug", "onClientMessage, new partial config for existing: {}".format(nodeAddr))
                        for c in message["config"]:
                            self.cbLog("debug", "onClientMessage, c in message[config]: {}".format(c))
                            self.nodeConfig[nodeAddr][c] = message["config"][c]
                    else:  # Partial config for a node we don't have any existing config for
                        self.cbLog("debug", "onClientMessage, new partial config for new: {}".format(nodeAddr))
                        self.nodeConfig[nodeAddr] = message["config"]
                    if nodeID not in self.configuring:
                        self.configuring.append(nodeID)  # Causes a start to be sent to node on complete config update
                    self.cbLog("debug", "onClentMessage, nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)))
                elif message["function"] == "send_battery":
                    self.cbLog("debug", "onClientMessage, send_battery for {}".format(message["id"]))
                    nodeAddr = self.id2addr[int(message["id"])]
                    if nodeAddr not in self.requestBatteries:
                        self.requestBatteries.append(nodeAddr)
                        self.cbLog("debug", "onClientMessage, added {} to requestBatteries".format(nodeAddr))
                    else:
                        self.cbLog("debug", "onClientMessage, requestBatteries for {}, but already one in queue".format(nodeAddr))
                elif message["function"] == "update_address":
                    nodeID = int(message["id"])
                    addr = int(message["address"])
                    self.id2addr[nodeID] = addr
                    self.addr2id[addr] = nodeID
                    self.save()
                elif message["function"] == "reset":
                    nodeAddr = self.id2addr[int(message["id"])]
                    msg = self.formatRadioMessage(nodeAddr, "reset", 0)
                    self.queueRadio(msg, nodeAddr, "reset")
        #except Exception as ex:
        #    self.cbLog("warning", "onClientMessage exception. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def sendConfig(self, nodeAddr):
        #self.cbLog("debug", "sendConfig, nodeAddr: " + str(nodeAddr) + ", nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)))
        #self.cbLog("debug", "sendConfig, type of nodeAddr: " + type(nodeAddr).__name__)
        formatMessage = ""
        messageCount = 0
        statesInConfig = False
        appValue = False
        appValueMessage = None
        if nodeAddr not in self.includeGrants: # Only send configuring message if not part of include_grant process
            #self.cbLog("debug", "sendConfig, sending configuring message to: {} ".format(nodeAddr))
            #msg = self.formatRadioMessage(nodeAddr, "configuring", 0, formatMessage)
            #self.queueRadio(msg, nodeAddr, "configuring")
            pass
        else:
            self.includeGrants.remove(nodeAddr)
        for m in self.nodeConfig[nodeAddr]:
            messageCount += 1
            self.cbLog("debug", "in m loop, m: " + m)
            aMessage = False
            if m[0] == "D":
                formatMessage = struct.pack("cBcB", "S", int(m[1:]), "R", 0)
                aMessage = True
            elif m == "name":
                line = "Spur button"
                stringLength = len(line) + 1
                formatString = "cBcBcBcBcB" + str(stringLength) + "sc"
                formatMessage = struct.pack(formatString, "S", 22, "R", 0, "F", 2, "Y", 10, "C", stringLength, str(line), "\00")
                line = self.nodeConfig[nodeAddr][m] 
                self.cbLog("debug", "name: " + line)
                stringLength = len(line) + 1
                formatString = "cBcB" + str(stringLength) + "sc"
                segment = struct.pack(formatString, "Y", 40, "C", stringLength, str(line), "\00")
                formatMessage += segment
                line = "Double-push to start"
                stringLength = len(line) + 1
                formatString = "cBcB" + str(stringLength) + "sc"
                segment = struct.pack(formatString, "Y", 70, "C", stringLength, str(line), "\00")
                formatMessage += segment
            elif m[0] == "S":
                statesInConfig = True
                self.cbLog("debug", "statesInConfig")
                s = self.nodeConfig[nodeAddr][m]
                self.cbLog("debug", "nodeConfig before changing: " + str(json.dumps(s, indent=4)))
                if "delayValue" in s:
                    if s["delayValue"] < 4:
                        s["delayValue"] = 4  # To prevent accidental delays of a day
                    s["delayValue"] = s["delayValue"] >> 1
                    if s["delayValue"] < 256:
                        s["delayMS"] = 0xFF
                    else:
                        s["delayMS"] = (s["delayValue"]  >> 8) & 0xFF 
                        s["delayValue"] = s["delayValue"] & 0xFF
                    if s["delayValue"] == 255:
                        s["delayValue"] = 254
                for f in ("SingleLeft", "SingleRight", "DoubleLeft", "DoubleRight", "messageValue", "messageState", \
                    "delayValue", "delayMS", "delayState", "appValue", "appState"):
                    if f not in s:
                        s[f] = 0xFF
                #self.cbLog("debug", "nodeConfig before sending: " + str(json.dumps(self.nodeConfig[nodeAddr][m], indent=4)))
                self.cbLog("debug", "nodeConfig before sending: " + str(json.dumps(s, indent=4)))
                formatMessage = struct.pack("cBBBBBBBBBBBBBBBB", "M", s["state"], s["state"], s["alert"], s["DoubleLeft"], \
                    s["SingleLeft"], 0xFF, 0xFF, s["SingleRight"], s["DoubleRight"], s["appValue"], s["appState"], \
                    s["delayValue"], s["delayState"], s["delayMS"], 0xFF, 0xFF)
            elif m == "app_value":
                appValue = True
                appValueMessage = struct.pack("cB", "A", self.nodeConfig[nodeAddr][m])
            if aMessage:
                self.cbLog("debug", "sendConfig, aMessage")
                display = base64.b64decode(self.nodeConfig[nodeAddr][m])
                #display = self.nodeConfig[nodeAddr][m]
                self.cbLog("debug", "sendConfig, display: {}".format(display))
                lines = display.split("\n")
                if "{{" in lines[0]:
                    del(lines[0])  #Remove a "special" first line
                firstSplit = None 
                numLines = len(lines)
                for l in lines:
                    if "|" in l:
                       self.cbLog("debug", "Line contains |")
                       if firstSplit is None:
                           firstSplit = lines.index(l)
                       ll = l.decode("utf-8").encode("latin-1", "ignore")
                       splitLine = ll.split("|")
                       for s in (0, 1):
                           splitLine[s] = splitLine[s].lstrip().rstrip()  # Removes whitespace
                           self.cbLog("debug", "After whitespace removed: " + str(splitLine[s]))
                           if len(splitLine[s]) > 0:
                               if splitLine[s][0] == "*":
                                   splitLine[s] = splitLine[s][1:]
                                   f = 3
                               else:
                                   f = 2
                           else:
                               f = 2
                           self.cbLog("debug", "sendConfig, font: {}, line: {}".format(f, ll))
                           stringLength = len(splitLine[s]) + 1
                           y_start =  Y_STARTS[numLines-1][lines.index(l)]
                           self.cbLog("debug", "sendConfig, string: " + splitLine[s] + ", length: " + str(stringLength))
                           self.cbLog("debug", "sendConfig, y_start: " + str(y_start))
                           formatString = "cBcBcB" + str(stringLength) + "sc"
                           if s == 0:
                               x = "l"
                           else:
                               x = "r"
                           segment = struct.pack(formatString, "F", f, "Y", y_start, x, stringLength, str(splitLine[s]), "\00")
                           self.cbLog("debug", "segment: " + str(segment.encode("hex")))
                           formatMessage += segment
                    else:
                        self.cbLog("debug", "sendConfig, line: " + str(l))
                        if len(l) > 0:
                            if l[0] == "*":
                                f = 3
                                ll = l[1:].decode("utf-8").encode("latin-1", "ignore")
                            else:
                                f = 2
                                ll = l.decode("utf-8").encode("latin-1", "ignore")
                        else:
                            f = 2
                            ll = l.decode("utf-8").encode("latin-1", "ignore")
                        self.cbLog("debug", "sendConfig, font: {}, line: {}".format(f, ll))
                        stringLength = len(ll) + 1
                        y_start =  Y_STARTS[numLines-1][lines.index(l)]
                        self.cbLog("debug", "sendConfig, y_start: " + str(y_start))
                        formatString = "cBcBcB" + str(stringLength) + "sc"
                        segment = struct.pack(formatString, "F", f, "Y", y_start, "C", stringLength, ll, "\00")
                        formatMessage += segment
                self.cbLog("debug", "sendConfig, firstSplit: " + str(firstSplit) + ", numLines: " + str(numLines))
                if firstSplit == 0:
                    segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 1, "B", 0x62, 0x5C, "X", 2, "Y", 2, "B", 0x60, 0x5A, \
                                            "X", 0x65, "Y", 1, "B", 0x62, 0x5C, "X", 0x66, "Y", 2, "B", 0x60, 0x5A)  
                    formatMessage += segment
                elif numLines == 4:
                    if firstSplit == 1:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x18, "B", 0x62, 0x48, "X", 2, "Y", 0x19, "B", 0x60, 0x46, \
                                            "X", 0x65, "Y", 0x18, "B", 0x62, 0x48, "X", 0x66, "Y", 0x19, "B", 0x60, 0x46)  
                    elif firstSplit == 2:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x2E, "B", 0x62, 0x30, "X", 2, "Y", 0x2F, "B", 0x60, 0x2E, \
                                            "X", 0x65, "Y", 0x2E, "B", 0x62, 0x30, "X", 0x66, "Y", 0x2F, "B", 0x60, 0x2E)  
                    elif firstSplit == 3:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x44, "B", 0x62, 0x18, "X", 2, "Y", 0x45, "B", 0x60, 0x16, \
                                            "X", 0x65, "Y", 0x44, "B", 0x62, 0x18, "X", 0x66, "Y", 0x45, "B", 0x60, 0x16)  
                    formatMessage += segment
                elif numLines == 3:
                    if firstSplit == 1:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x1E, "B", 0x62, 0x40, "X", 2, "Y", 0x1F, "B", 0x60, 0x3E, \
                                            "X", 0x65, "Y", 0x1E, "B", 0x62, 0x40, "X", 0x66, "Y", 0x1F, "B", 0x60, 0x3E)  
                    elif firstSplit == 2:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x44, "B", 0x62, 0x18, "X", 2, "Y", 0x45, "B", 0x60, 0x16, \
                                            "X", 0x65, "Y", 0x44, "B", 0x62, 0x18, "X", 0x66, "Y", 0x45, "B", 0x60, 0x16)  
                    formatMessage += segment
                elif numLines == 2:
                    if firstSplit == 1:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x30, "B", 0x62, 0x2F, "X", 2, "Y", 0x31, "B", 0x60, 0x2D, \
                                            "X", 0x65, "Y", 0x30, "B", 0x62, 0x2F, "X", 0x66, "Y", 0x31, "B", 0x60, 0x2D)  
                    formatMessage += segment
                segment = struct.pack("cc", "E", "S") 
                formatMessage += segment
            if not appValue:  # Ensures that app_value is sent last
                self.cbLog("debug", "Sending to node: {}".format(formatMessage))
                self.cbLog("debug", "Sending to node: " + str(formatMessage.encode("hex")))
                wakeup = 0
                msg = self.formatRadioMessage(nodeAddr, "config", wakeup, formatMessage)
                self.queueRadio(msg, int(nodeAddr), "config")
            else:
                appValue = False
        if appValueMessage:
            try:
                self.cbLog("debug", "Sending app_value to node: {}".format(appValueMessage))
                self.cbLog("debug", "Sending app_value to node: " + str(appValueMessage.encode("hex")))
                self.wakeupCount[nodeAddr] = 0
                msg = self.formatRadioMessage(nodeAddr, "config", 30, appValueMessage)  # Wakeup after 30s when changing current screen
                self.queueRadio(msg, int(nodeAddr), "config")
            except Exception as ex:
                self.cbLog("warning", "sendConfig, expection sending app_value. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
        nodeID =  self.addr2id[nodeAddr]
        try:
            if nodeID in list(self.configuring):
                self.cbLog("debug", "Removing nodeID " + str(nodeID) + " from " + str(self.configuring))
                if not appValueMessage:
                    msg = self.formatRadioMessage(nodeAddr, "start", PRESSED_WAKEUP, formatMessage)
                    self.queueRadio(msg, nodeAddr, "start")
                self.configuring.remove(nodeID)
        except Exception as ex:
            self.cbLog("warning", "sendConfig, expection in removing from self.configuring. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
        self.cbLog("debug", "sendConfig statesInConfig: {}".format(statesInConfig))
        if statesInConfig:
            self.wakeupCount[nodeAddr] = 0
            for m in self.nodeConfig[nodeAddr]:
                if m[0] == "S":
                    if "wakeup" in self.nodeConfig[nodeAddr][m]:
                        self.cbLog("debug", "sendConfig nodeConfig-alert: {}".format(self.nodeConfig[nodeAddr][m]["alert"]))
                        self.cbLog("debug", "sendConfig nodeConfig-wakeup: {}".format(self.nodeConfig[nodeAddr][m]["wakeup"]))
                        self.cbLog("debug", "sendConfig type of nodeAddr: {}".format(type(nodeAddr)))
                        if nodeAddr not in self.wakeups:
                            self.wakeups[nodeAddr] = {}
                        self.wakeups[nodeAddr][self.nodeConfig[nodeAddr][m]["alert"]] = self.nodeConfig[nodeAddr][m]["wakeup"]
                    else:
                        if nodeAddr not in self.wakeups:
                            self.wakeups[nodeAddr] = {}
                        self.wakeups[nodeAddr][self.nodeConfig[nodeAddr][m]["alert"]] = [DEFAULT_WAKEUP_INTERVAL]
            self.cbLog("debug", "sendConfig added to wakeups nodeAddr: {}, nodeID: {}".format(nodeAddr, nodeID))
            self.cbLog("debug", "sendConfig wakeups: " + str(json.dumps(self.wakeups, indent=4)))
            self.cbLog("debug", "sendConfig wakeupCount: " + str(json.dumps(self.wakeupCount, indent=4)))
        del(self.nodeConfig[nodeAddr])
        self.sendingConfig.remove(nodeAddr)

    def requestBattery(self, nodeAddr):
        self.cbLog("info", "Battery/RSSI requested from {}".format(nodeAddr))
        self.requestBatteries.remove(nodeAddr)
        msg = self.formatRadioMessage(nodeAddr, "send_battery", self.setWakeup(nodeAddr))
        self.queueRadio(msg, nodeAddr, "send_battery")

    def findRSSI(self, source, nodeID=None):
        self.findingRssiAddr = source
        msg= {
            "id": self.id,
            "request": "command",
            "command": "get_rssi"
        }
        self.sendMessage(msg, self.adaptor)

    def onRSSI(self, rssi):
        self.cbLog("debug", "RSSI for {}: {}, type: {}".format(self.findingRssiAddr, rssi, type(self.findingRssiAddr)))
        msg = {
            "function": "rssi",
            "address": self.findingRssiAddr,
            "rssi": rssi
        }
        if self.findingRssiAddr in self.addr2id:
            msg["buttonId"] = self.addr2id[self.findingRssiAddr]
        self.findingRssiAddr = None
        self.client.send(msg)

    def onRadioMessage(self, message):
        if self.radioOn:
            self.cbLog("debug", "onRadioMessage, length: {}".format(len(message)))
            if len(message) < 6:
                return
            try:
                destination = struct.unpack(">H", message[0:2])[0]
            except Exception as ex:
                self.cbLog("warning", "onRadioMessage. Malformed radio message. Type: {}, exception: {}".format(type(ex), ex.args))
                return
            self.cbLog("debug", "Rx: destination: " + str("{0:#0{1}X}".format(destination,6)))
            source, hexFunction = struct.unpack(">HB", message[2:5])
            try:
                function = (key for key,value in FUNCTIONS.items() if value==hexFunction).next()
            except:
                function = "undefined"
            self.cbLog("debug", "source: {}, function: {}".format(source, function))
            if function == "woken_up" and destination != SPUR_ADDRESS:
                if "source" in self.addr2id:
                    nodeID = self.addr2id[source]
                else:
                    nodeID = None
                self.findRSSI(source, nodeID)
            if (function == "include_req" and destination != SPUR_ADDRESS):
                self.cbLog("debug", "include_req for a different bridge")
                payload = message[10:14]
                nodeID = struct.unpack(">I", payload)[0]
                self.findRSSI(source, nodeID)
            #if (destination == SPUR_ADDRESS) and (source not in self.addr2id) and source != 0:
            if (destination == SPUR_ADDRESS):
                #hexMessage = message.encode("hex")
                #self.cbLog("debug", "hex message after decode: " + str(hexMessage))
                self.cbLog("debug", "Rx: " + function + " from button: " + str("{0:#0{1}x}".format(source,6)))
                if function == "include_req":
                    length = struct.unpack(">b", message[9])[0]
                    if length == 14:
                        payload = message[10:14]
                        nodeID = struct.unpack(">I", payload)[0]
                        version = 0
                        rssi = 0
                    else:
                        payload = message[10:16]
                        (nodeID, version, rssi) = struct.unpack(">Ibb", payload)
                    hexPayload = payload.encode("hex")
                    self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    self.cbLog("debug", "Rx, include_req, nodeID: " + str(nodeID))
                    msg = {
                        "function": "include_req",
                        "include_req": nodeID,
                        "version": version,
                        "rssi": rssi
                    }
                    self.client.send(msg)
                    self.cbLog("debug", "removing all references to nodeID {}".format(nodeID))
                    self.removeNodeMessages(nodeID)
                    if nodeID not in list(self.including):
                        self.including.append(nodeID)
                elif function == "alert":
                    length = struct.unpack(">b", message[9])[0]
                    if length == 14:
                        payload = message[10:14]
                        try:
                            (alertType, rssi, temperature) = struct.unpack(">Hbb", payload)
                        except Exception as ex:
                            alertType = 0xFFFF
                            self.cbLog("warning", "Unknown alert type received. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
                    else:
                        payload = message[10:12]
                        try:
                            alertType = struct.unpack(">H", payload)[0]
                        except Exception as ex:
                            alertType = 0xFFFF
                            self.cbLog("warning", "Unknown alert type received. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
                    hexPayload = payload.encode("hex")
                    self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    self.cbLog("debug", "Rx, alert, type: {}".format(alertType & 0xFF00))
                    if (alertType & 0xFF00) == 0x0200:
                        battery_level = ((alertType & 0xFF) * 0.235668)/10
                        msg = {
                            "function": "battery",
                            "value": battery_level,
                            "source": self.addr2id[source]
                        }
                        if length == 14:
                            msg["rssi"] = rssi
                            msg["temperature"] = temperature
                    else:
                        self.cbLog("debug", "onRadioMessage, resetting wakeupCount for {}, id: {}".format(source, self.addr2id[source]))
                        self.buttonState[source] = alertType & 0xFF
                        self.wakeupCount[source] = 0
                        msg = {
                            "function": "alert",
                            "type": alertType,
                            "source": self.addr2id[source]
                        }
                    self.client.send(msg)
                    # Uncomment appropriately to test nack
                    #self.cbLog("debug", "onRadioMessage, ackCount: {}".format(self.ackCount))
                    #if self.ackCount == 3 or self.ackCount == 6:
                    #    self.cbLog("debug", "onRadioMessage, sending Nack")
                    #    msg = self.formatRadioMessage(source, "nack", self.setWakeup(source))
                    #    self.queueRadio(msg, source, "nack")
                    #else:
                    if True:
                        msg = self.formatRadioMessage(source, "ack", self.setWakeup(source))
                        self.queueRadio(msg, source, "ack")
                    #self.ackCount += 1
                elif function == "woken_up":
                    self.cbLog("debug", "Rx, woken_up from id: {}".format(self.addr2id[source]))
                    msg = self.formatRadioMessage(source, "ack", self.setWakeup(source))
                    self.queueRadio(msg, source, "ack")
                    msg = {
                        "function": "woken_up",
                        "source": self.addr2id[source]
                    }
                    self.client.send(msg)
                elif function == "ack":
                    self.onAck(source)
                else:
                    self.cbLog("warning", "onRadioMessage, undefined message, source " + str(source) + ", function: " + function)

    def setWakeup(self, nodeAddr):
        nodeID = self.addr2id[nodeAddr]
        """
        self.testCount += 1
        if (self.testCount > 15) and (self.testCount < 30):
            wakeup = 0
            return wakeup
        """
        wakeup = -1
        self.cbLog("debug", "setWakeup, nodeAddr: {}, id: {}, buttonState: {}".format(nodeAddr, nodeID, self.buttonState))
        self.cbLog("debug", "setWakeup, self.nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)) + ", self.configuring: " + str(self.configuring))
        self.cbLog("debug", "setWakeup, requestBatteries: {}".format(self.requestBatteries))
        if (nodeAddr in self.nodeConfig) or (nodeID in self.configuring) or (nodeAddr in self.requestBatteries):
            wakeup = 0;
            self.nextWakeupTime[nodeAddr] = int(time.time() + 720)  # Time to allow before excluding when configuring
            self.cbLog("debug", "setWakeup 0 (1) for {}, now: {}, next wakeup: {}".format(nodeID, time.time(), self.nextWakeupTime[nodeAddr]))
        else:
            #self.cbLog("debug", "setWakeup, messageQueue (2): " + str(json.dumps(self.messageQueue, indent=4)))
            for m in self.messageQueue:
                if m["destination"] == nodeAddr:
                    wakeup = 0;
                    self.nextWakeupTime[nodeAddr] = int(time.time() + 720)  # Time to allow before excluding when messages in queue
                    self.cbLog("debug", "setWakeup 0 (2) for {}, now: {}, next wakeup: {}".format(nodeID, time.time(), self.nextWakeupTime[nodeAddr]))
                    break
            if wakeup == -1:
                try:
                    self.cbLog("debug", "setWakeup buttonState: {}, wakeupCount: {}".format(self.buttonState[nodeAddr], self.wakeupCount[nodeAddr]))
                    wakeup = self.wakeups[nodeAddr][self.buttonState[nodeAddr]][self.wakeupCount[nodeAddr]]
                    self.nextWakeupTime[nodeAddr] = int(time.time() + wakeup*2*GRACE_TIME_MULT)
                    self.cbLog("debug", "setWakeup (-1) for {}, now: {}, next wakeup: {}".format(nodeID, time.time(), self.nextWakeupTime[nodeAddr]))
                except Exception as ex:
                    self.cbLog("warning", "setWakeup, problem setting next wakeup for {}. Type: {}. Exception: {}".format(nodeAddr, type(ex), ex.args))
                    wakeup = 7200
                    try:
                        self.nextWakeupTime[nodeAddr] = int(time.time() + wakeup*2*GRACE_TIME_MULT)
                    except Exception as ex:
                        self.cbLog("warning", "setWakeup, problem setting nextWakeupTime for {}. Type: {}. Exception: {}".format(nodeAddr, type(ex), ex.args))
                try:
                    self.wakeupCount[nodeAddr] += 1
                    if self.wakeupCount[nodeAddr] >=  len(self.wakeups[nodeAddr][self.buttonState[nodeAddr]]):
                        self.wakeupCount[nodeAddr] = len(self.wakeups[nodeAddr][self.buttonState[nodeAddr]]) - 1
                except Exception as ex:
                    self.cbLog("warning", "setWakeup, problem incrementing wakeup for {}. Type: {}. Exception: {}".format(nodeAddr, type(ex), ex.args))
        if (nodeAddr in self.nodeConfig) and (nodeAddr not in self.sendingConfig):
            reactor.callLater(3, self.sendConfig, nodeAddr)  # Delay of 3 gives time for node to update screen and turn on radio
            self.sendingConfig.append(nodeAddr)
        return wakeup

    def onAck(self, source):
        """ If there is no more data to send, we need to send an ack with a normal wakeup 
            time to ensure that the node goes to sleep.
        """
        self.cbLog("debug", "onAck, source: {}".format(source))
        #self.cbLog("debug", "onAck, source: " + str("{0:#0{1}x}".format(source,6)))
        #self.cbLog("debug", "onAck, messageQueue: " + str(json.dumps(self.messageQueue, indent=4)))
        #self.cbLog("debug", "onAck, source: " + str(source) + ", self.sentTo: " + str(self.sentTo))
        if source in self.sentTo:
            moreToCome = False
            for m in list(self.messageQueue):
                if m["destination"] == source:
                    if m["attempt"] > 0:
                        self.cbLog("debug", "onAck, removing message: " + m["function"] + " for: " + str(source) + ", id:" + str(self.addr2id[source]))
                        self.messageQueue.remove(m)
                        self.sentTo.remove(source)
                    else:
                        moreToCome = True
            if not moreToCome and (self.addr2id[source] not in self.configuring):
                msg = self.formatRadioMessage(source, "ack", PRESSED_WAKEUP)  # Shorter wakeup immediately after config
                self.queueRadio(msg, source, "ack")
        else:
            self.cbLog("warning", "onAck, received ack from node that does not correspond to a sent message: " + str(source))

    def beacon(self):
        if self.beaconCalled == self.beaconInterval:
            self.sendQueued(True)
            self.beaconCalled = 0
            self.beaconInterval = random.randrange(10, 14, 2)
            #self.cbLog("debug", "beaconInterval: {}".format(self.beaconInterval))
        else:
            self.beaconCalled += 1
            self.sendQueued(False)
        reactor.callLater(0.5, self.beacon)

    def monitor(self):
        now = time.time()
        #self.cbLog("debug", "monitor, nextWakeupTimes: {}". format(self.nextWakeupTime))
        for n in self.addr2id:
            if n in list(self.nextWakeupTime):
                if (now > self.nextWakeupTime[n]):
                    self.cbLog("debug", "monitor, excluding {}, now: {}, nextWakeupTime: {}".format(n, now, self.nextWakeupTime[n]))
                    msg = {
                        "function": "exclude_req",
                        "source": self.addr2id[n]
                    }
                    self.client.send(msg)
                    del self.nextWakeupTime[n]
        reactor.callLater(MONITOR_INTERVAL, self.monitor)

    def removeNodeMessages(self, nodeID):
        #Remove all queued messages and reference to a node if we get a new include_req
        try:
            self.cbLog("debug", "removeNodeMessages, nodeID: {}, address: {}".format(nodeID, self.id2addr[nodeID]))
            self.cbLog("debug", "removeNodeMessages, messageQueue: {}".format(self.messageQueue))
            if nodeID in self.id2addr:
                addr = self.id2addr[nodeID]
                for m in list(self.messageQueue):
                    if m["destination"] == addr:
                        self.messageQueue.remove(m)
                        self.cbLog("debug", "removeNodeMessages: " + str(nodeID) + ", removed: " + m["function"])
                if addr in self.nodeConfig:
                    del self.nodeConfig[addr]
                #if addr in self.buttonState:
                #    del self.buttonState[addr]
                if self.id2addr[nodeID] in self.sentTo:
                    self.sentTo.remove(addr)
                    self.cbLog("debug", "removeNodeMessages,removed from sentTo: {}".format(nodeID))
                    #del self.id2addr[nodeID]
                #if addr in self.addr2id:
                #    del self.addr2id[addr]
                #if addr in self.wakeupCount:
                #    del self.wakeupCount[addr]
                #if addr in self.wakeups:
                #    del self.wakeups[addr]
        except Exception as ex:
            self.cbLog("warning", "removeNodeMessages, cannot remove messages for {}. Type: {}, exception: {}".format(nodeID, type(ex), ex.args))

    def sendQueued(self, beacon):
        """
        In frames where a beacon is sent, don't send anything else apart from acks.
        """
        if self.findingRssiAddr:
            return
        now = time.time()
        sentLength = 0
        removed = False  # Stops sending more messages if node has just been removed
        sentAck = []
        for m in list(self.messageQueue):
            #self.cbLog("debug", "sendQueued: {}, {}, sentTo: {}".format(m["destination"], m["function"], self.sentTo))
            if (sentLength < 60) and not removed:   # Send max of 60 bytes in a frame if more than one message sent
                if ((m["function"] == "ack") and (m["destination"] not in sentAck)) or (m["function"] == "include_not"):
                    self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]))
                    self.sendMessage(m["message"], self.adaptor)
                    self.messageQueue.remove(m)  # Only send ack and include_not once
                    sentAck.append(m["destination"])
                    sentLength += m["message"]["length"]
                    if m["destination"] in self.requestBatteries:  # Wait until an ack has been sent before requesting battery
                        self.requestBattery(m["destination"])
                elif (m["destination"] not in self.sentTo) and (m["destination"] not in sentAck) and not beacon:
                    self.sendMessage(m["message"], self.adaptor)
                    self.sentTo.append(m["destination"])
                    m["sentTime"] = now
                    m["attempt"] = 1
                    self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]) + ", attempt " + str(m["attempt"]))
                    sentLength += m["message"]["length"]
                elif (now - m["sentTime"] > 9) and (m["destination"] not in sentAck) and (m["attempt"] > 0) and not beacon:
                    if m["attempt"] > 5:
                        self.sentTo.remove(m["destination"])
                        self.removeNodeMessages(self.addr2id[m["destination"]])
                        removed = True
                        self.cbLog("debug", "sendQueued: No ack, removed: " + m["function"] + ", for " + str(m["destination"]))
                    else:
                        self.sendMessage(m["message"], self.adaptor)
                        m["sentTime"] = now
                        m["attempt"] += 1
                        self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]) + ", attempt " + str(m["attempt"]))
                        sentLength += m["message"]["length"]
        if beacon and (sentLength == 0):
            msg = self.formatRadioMessage(0xBBBB, "beacon", 0)
            self.sendMessage(msg, self.adaptor)

    def formatRadioMessage(self, destination, function, wakeupInterval, data = None):
        try:
            timeStamp = 0x00000000
            if function != "beacon":
                length = 4
            else:
                length = 10
            if data:
                length += len(data)
                #self.cbLog("debug", "data length: " + str(length))
            m = ""
            m += struct.pack(">H", destination)
            m += struct.pack(">H", SPUR_ADDRESS)
            if function != "beacon":
                m+= struct.pack("B", FUNCTIONS[function])
                m+= struct.pack("B", length)
                m+= struct.pack("I", timeStamp)
                m+= struct.pack(">H", wakeupInterval)
                self.cbLog("debug", "formatRadioMessage, wakeupInterval: " +  str(wakeupInterval))
            #self.cbLog("debug", "length: " +  str(length))
            if data:
                m += data
            length = len(m)
            hexPayload = m.encode("hex")
            self.cbLog("debug", "formatRadioMessage, message: " + str(hexPayload))
            msg= {
                "id": self.id,
                "length": length,
                "request": "command",
                "data": base64.b64encode(m)
            }
            return msg
        except Exception as ex:
            self.cbLog("warning", "Problem formatting message. Exception: " + str(type(ex)) + ", " + str(ex.args))

    def queueRadio(self, msg, destination, function):
        self.cbLog("debug", "queueRadio, queuing {} for {}".format(function, destination))
        toQueue = {
            "message": msg,
            "destination": destination,
            "function": function,
            "attempt": 0,
            "sentTime": 0
        }
        self.messageQueue.append(toQueue)

    def onAdaptorService(self, message):
        #self.cbLog("debug", "onAdaptorService, message: " + str(message))
        for p in message["service"]:
            if p["characteristic"] == "spur":
                req = {"id": self.id,
                       "request": "service",
                       "service": [
                                   {"characteristic": "spur",
                                    "channel": 6,
                                    "interval": 0
                                   },
                                   {"characteristic": "rssi",
                                    "interval": 0
                                   }
                                  ]
                      }
                self.sendMessage(req, message["id"])
                self.adaptor = message["id"]
        self.setState("running")
        if not self.beaconRunning:
            self.beaconRunning = True
            reactor.callLater(BEACON_START_DELAY, self.beacon)
        reactor.callLater(MONITOR_INTERVAL, self.monitor)

    def onAdaptorData(self, message):
        #self.cbLog("debug", "onAdaptorData, message: " + str(message))
        if message["characteristic"] == "spur":
            self.onRadioMessage(base64.b64decode(message["data"]))
        elif message["characteristic"] == "rssi":
            self.onRSSI(message["data"])

    def readLocalConfig(self):
        global config
        try:
            with open(configFile, 'r') as f:
                newConfig = json.load(f)
                self.cbLog("debug", "Read local config")
                config.update(newConfig)
        except Exception as ex:
            self.cbLog("warning", "Problem reading config. Type: " + str(type(ex)) + ", exception: " +  str(ex.args))
        self.cbLog("debug", "Config: " + str(json.dumps(config, indent=4)))

    def onConfigureMessage(self, managerConfig):
        #self.readLocalConfig()
        self.client = CbClient(self.id, CID, 3)
        self.client.onClientMessage = self.onClientMessage
        self.client.sendMessage = self.sendMessage
        self.client.cbLog = self.cbLog
        self.saveFile = CB_CONFIG_DIR + self.id + ".savestate"
        self.loadSaved()
        reactor.callLater(CHECK_INTERVAL, self.checkConnected)
        self.setState("starting")

if __name__ == '__main__':
    App(sys.argv)
