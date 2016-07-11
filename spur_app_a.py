#!/usr/bin/env python
# spur_app_a.py
"""
Copyright (c) 2015 ContinuumBridge Limited
"""

import sys
import time
import json
import pickle
import struct
import base64
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
    "start": 0x0B
}
ALERTS = {
    0x0000: "left_short",
    0x0001: "right_short",
    0x0002: "left_long",
    0x0003: "right_long",
    0x0200: "battery"
}
MESSAGE_NAMES = (
    "normalMessage",
    "pressedMessage",
    "overrideMessage",
    "override"
)

Y_STARTS = (
    (38, 0, 0 ,0, 0),
    (18, 56, 0, 0, 0),
    (10, 40, 70, 0, 0),
    (4, 26, 48, 70, 0),
    (0, 20, 40, 60, 80)
);

SPUR_ADDRESS = int(os.getenv('CB_SPUR_ADDRESS', '0x0000'), 16)
CHECK_INTERVAL      = 30*60
CID                 = "CID157"           # Client ID
GRANT_ADDRESS       = 0xBB00
NORMAL_WAKEUP       = 60*60*2                # How long node should sleep for, seconds/2
#NORMAL_WAKEUP       = 30                # How long node should sleep for in normal state, seconds/2
PRESSED_WAKEUP      = 5*60              # How long node should sleep for in pressed state, seconds/2
config              = {
                        "nodes": [ ]
}

class App(CbApp):
    def __init__(self, argv):
        self.appClass       = "control"
        self.state          = "stopped"
        self.id2addr        = {}          # Node id to node address mapping
        self.addr2id        = {}          # Node address to node if mapping
        self.maxAddr        = 0
        self.radioOn        = True
        self.messageQueue   = []
        self.sentTo         = []
        self.nodeConfig     = {} 
        self.beaconCalled   = 0
        self.including      = []
        self.sendingConfig  = []
        self.buttonState    = {}

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
            "maxAddr": self.maxAddr,
            "buttonState": self.buttonState
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
                self.maxAddr = state["maxAddr"]
                self.buttonState = state["buttonState"]
        except Exception as ex:
            self.cbLog("warning", "Problem loading saved state. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
        #finally:
        #    try:
        #        os.remove(self.saveFile)
        #        self.cbLog("debug", "deleted saved state file")
        #    except Exception as ex:
        #        self.cbLog("debug", "Cannot remove saved state file. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

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
                    nodeID = int(message["node"])
                    if nodeID not in self.id2addr:
                        self.maxAddr += 1
                        self.id2addr[nodeID] = self.maxAddr
                        self.cbLog("debug", "id2addr: " + str(self.id2addr))
                        self.addr2id[self.maxAddr] = nodeID
                        self.cbLog("debug", "addr2id: " + str(self.addr2id))
                        self.buttonState[self.maxAddr] = 0xFF
                        self.save()
                    data = struct.pack(">IH", nodeID, self.id2addr[nodeID])
                    msg = self.formatRadioMessage(GRANT_ADDRESS, "include_grant", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
                    self.queueRadio(msg, self.id2addr[nodeID], "include_grant")
                elif message["function"] == "config":
                    self.cbLog("debug", "onClientMessage, id2addr: " + str(self.id2addr))
                    self.cbLog("debug", "onClientMessage, addr2id: " + str(self.addr2id))
                    self.cbLog("debug", "onClientMessage, message[node]: " + str(message["node"]))
                    self.cbLog("debug", "onClientMessage, message[config]: " + str(json.dumps(message["config"], indent=4)))
                    self.nodeConfig[self.id2addr[int(message["node"])]] = message["config"]
                    self.cbLog("debug", "onClentMessage, nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)))
        #except Exception as ex:
        #    self.cbLog("warning", "onClientMessage exception. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def sendConfig(self, nodeAddr):
        self.cbLog("debug", "sendConfig, nodeAddr: " + str(nodeAddr) + ", nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)))
        #self.cbLog("debug", "sendConfig, type of nodeAddr: " + type(nodeAddr).__name__)
        formatMessage = ""
        messageCount = 0
        for m in self.nodeConfig[nodeAddr]:
            messageCount += 1
            self.cbLog("debug", "in m loop, m: " + m)
            aMessage = False
            if m == "normalMessage":
                formatMessage = struct.pack("cBcBcB", "S", 1, "R", 0, "F", 2)
                aMessage = True
            elif m == "pressedMessage":
                formatMessage = struct.pack("cBcBcB", "S", 2, "R", 0, "F", 2)
                aMessage = True
            elif m == "overrideMessage":
                formatMessage = struct.pack("cBcBcB", "S", 3, "R", 0, "F", 2)
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
                s = self.nodeConfig[nodeAddr][m]
                formatMessage = struct.pack("cBBBBBBBBBBBBBBBB", "M", s["state"], s["display"], s["alert"], s["left-double"], \
                    s["left-single"], 0xFF, 0xFF, s["right-single"], s["right-double"], s["message-value"], s["message-state"], \
                    s["wait-value"], s["wait-state"], 0xFF, 0xFF, 0xFF)
            elif m == "app_value":
                formatMessage = struct.pack("cB", "C", self.nodeConfig[nodeAddr][m])
            if aMessage:
                lines = self.nodeConfig[nodeAddr][m].split("\n")
                firstSplit = None 
                numLines = len(lines)
                for l in lines:
                    if "|" in l:
                       self.cbLog("debug", "Line contains |")
                       if firstSplit is None:
                           firstSplit = lines.index(l)
                       splitLine = l.split("|")
                       for s in (0, 1):
                           splitLine[s] = splitLine[s].lstrip().rstrip()  # Removes whitespace
                           self.cbLog("debug", "After whitespace removed: " + str(splitLine[s]))
                           stringLength = len(splitLine[s]) + 1
                           y_start =  Y_STARTS[numLines-1][lines.index(l)]
                           self.cbLog("debug", "sendConfig, string: " + splitLine[s] + ", length: " + str(stringLength))
                           self.cbLog("debug", "sendConfig, y_start: " + str(y_start))
                           formatString = "cBcB" + str(stringLength) + "sc"
                           if s == 0:
                               x = "l"
                           else:
                               x = "r"
                           segment = struct.pack(formatString, "Y", y_start, x, stringLength, str(splitLine[s]), "\00")
                           self.cbLog("debug", "segment: " + str(segment.encode("hex")))
                           formatMessage += segment
                    else:
                        self.cbLog("debug", "sendConfig, line: " + str(l))
                        stringLength = len(l) + 1
                        y_start =  Y_STARTS[numLines-1][lines.index(l)]
                        self.cbLog("debug", "sendConfig, y_start: " + str(y_start))
                        formatString = "cBcB" + str(stringLength) + "sc"
                        segment = struct.pack(formatString, "Y", y_start, "C", stringLength, str(l), "\00")
                        formatMessage += segment
                self.cbLog("debug", "sendConfig, firstSplit: " + str(firstSplit) + ", numLines: " + str(numLines))
                if firstSplit == 0:
                    segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 1, "B", 0x62, 0x5C, "X", 2, "Y", 2, "B", 0x60, 0x5A, \
                                            "X", 0x65, "Y", 1, "B", 0x62, 0x5C, "X", 0x66, "Y", 2, "B", 0x60, 0x5A)  
                    formatMessage += segment
                elif numLines == 4:
                    if firstSplit == 1:
                        segment = struct.pack("cBcBcBBcBcBcBBcBcBcBBcBcBcBB", "X", 1, "Y", 0x18, "B", 0x62, 0x44, "X", 2, "Y", 0x19, "B", 0x60, 0x42, \
                                            "X", 0x65, "Y", 0x18, "B", 0x62, 0x44, "X", 0x66, "Y", 0x19, "B", 0x60, 0x42)  
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
            self.cbLog("debug", "Sending to node: " + str(formatMessage.encode("hex")))
            wakeup = 0
            msg = self.formatRadioMessage(nodeAddr, "config", wakeup, formatMessage)
            self.queueRadio(msg, int(nodeAddr), "config")
        nodeID = self.addr2id[nodeAddr]
        try:
            self.cbLog("debug", "Should I remove " + str(nodeID) + " from " + str(self.including))
            if nodeID in list(self.including):
                self.cbLog("debug", "nodeID " + str(nodeID) + " should be removed from " + str(self.including))
                msg = self.formatRadioMessage(nodeAddr, "start", 0, formatMessage)
                self.queueRadio(msg, nodeAddr, "start")
                self.requestBattery(nodeAddr)
                self.including.remove(nodeID)
        except Exception as ex:
            self.cbLog("warning", "sendConfig, expection in removing from self.including. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
        del(self.nodeConfig[nodeAddr])
        self.sendingConfig.remove(nodeAddr)

    def requestBattery(self, nodeAddr):
        msg = self.formatRadioMessage(nodeAddr, "send_battery", self.setWakeup(nodeAddr))
        self.queueRadio(msg, nodeAddr, "send_battery")

    def onRadioMessage(self, message):
        if self.radioOn:
            self.cbLog("debug", "onRadioMessage")
            destination = struct.unpack(">H", message[0:2])[0]
            #self.cbLog("debug", "Rx: destination: " + str("{0:#0{1}X}".format(destination,6)))
            if destination == SPUR_ADDRESS:
                source, hexFunction, length = struct.unpack(">HBB", message[2:6])
                try:
                    function = (key for key,value in FUNCTIONS.items() if value==hexFunction).next()
                except:
                    function = "undefined"
                if (source not in self.addr2id) and source != 0:
                    self.cbLog("warning", "Radio message for node at unallocated address: " + str(source))
                    return
                #hexMessage = message.encode("hex")
                #self.cbLog("debug", "hex message after decode: " + str(hexMessage))
                self.cbLog("debug", "Rx: " + function + " from button: " + str("{0:#0{1}x}".format(source,6)))

                if function == "include_req":
                    payload = message[10:14]
                    hexPayload = payload.encode("hex")
                    self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    nodeID = struct.unpack(">I", payload)[0]
                    self.cbLog("debug", "Rx, include_req, nodeID: " + str(nodeID))
                    msg = {
                        "function": "include_req",
                        "include_req": nodeID
                    }
                    self.client.send(msg)
                    if nodeID not in list(self.including):
                        self.cbLog("debug", "nodeID " + str(nodeID) + " should be removed from " + str(self.including))
                        self.including.append(nodeID)
                    else:
                        self.removeNodeMessages(nodeID)
                elif function == "alert":
                    payload = message[10:12]
                    #hexPayload = payload.encode("hex")
                    #self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    try:
                        alertType = struct.unpack(">H", payload)[0]
                    except Exception as ex:
                        alertType = 0xFFFF
                        self.cbLog("warning", "Unknown alert type received. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
                    self.cbLog("debug", "Rx, alert, type: " + str(alertType))
                    if (alertType & 0xFF00) == 0x200:
                        battery_level = ((alertType & 0xFF) * 0.235668)/10
                        self.cbLog("debug", "Battery level for " + str(self.addr2id[source]) + ": " + str(battery_level))
                        msg = {
                            "function": "battery",
                            "value": battery_level,
                            "signal": 5, 
                            "source": self.addr2id[source]
                        }
                    else:    
                        self.buttonState[source] = alertType & 0xFF
                        msg = {
                            "function": "alert",
                            "type": alertType,
                            "signal": 5, 
                            "source": self.addr2id[source]
                        }
                    self.client.send(msg)
                    msg = self.formatRadioMessage(source, "ack", self.setWakeup(source))
                    self.queueRadio(msg, source, "ack")
                elif function == "woken_up":
                    self.cbLog("debug", "Rx, woken_up")
                    msg = self.formatRadioMessage(source, "ack", self.setWakeup(source))
                    self.queueRadio(msg, source, "ack")
                    msg = {
                        "function": "woken_up",
                        "signal": 5, 
                        "source": self.addr2id[source]
                    }
                    self.client.send(msg)
                elif function == "ack":
                    self.onAck(source)
                else:
                    self.cbLog("warning", "onRadioMessage, undefined message, source " + str(source) + ", function: " + function)

    def setWakeup(self, nodeAddr):
        self.cbLog("debug", "setWakeup, nodeAddr: " + str(nodeAddr) + ", self.buttonState: " + str(self.buttonState))
        if self.buttonState[nodeAddr] == 0x01:
            wakeup = PRESSED_WAKEUP
        else:
            wakeup = NORMAL_WAKEUP
        self.cbLog("debug", "setWakeup, self.nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)) + ", self.including: " + str(self.including))
        if (nodeAddr in self.nodeConfig) or (self.addr2id[nodeAddr] in self.including):
            wakeup = 0;
            self.cbLog("debug", "wakeup = 0 (1)")
        else:
            for m in self.messageQueue:
                if m["destination"] == nodeAddr:
                    wakeup = 0;
                    self.cbLog("debug", "wakeup = 0 (2)")
        if (nodeAddr in self.nodeConfig) and (nodeAddr not in self.sendingConfig):
            reactor.callLater(1, self.sendConfig, nodeAddr)
            self.sendingConfig.append(nodeAddr)
        return wakeup

    def onAck(self, source):
        """ If there is no more data to send, we need to send an ack with a normal wakeup 
            time to ensure that the node goes to sleep.
        """
        self.cbLog("debug", "onAck, source: " + str("{0:#0{1}x}".format(source,6)))
        #self.cbLog("debug", "onAck, messageQueue: " + str(json.dumps(self.messageQueue, indent=4)))
        #self.cbLog("debug", "onAck, source: " + str(source) + ", self.sentTo: " + str(self.sentTo))
        if source in self.sentTo:
            moreToCome = False
            for m in list(self.messageQueue):
                if m["destination"] == source:
                    if m["attempt"] > 0:
                        self.cbLog("debug", "onAck, removing message: " + m["function"] + " for: " + str(source))
                        self.messageQueue.remove(m)
                        self.sentTo.remove(source)
                    else:
                        moreToCome = True
            if not moreToCome and not self.addr2id[source] in self.including:
                msg = self.formatRadioMessage(source, "ack", PRESSED_WAKEUP)  # Shorter wakeup immediately after config
                self.queueRadio(msg, source, "ack")
        else:
            self.cbLog("warning", "onAck, received ack from node that does not correspond to a sent message: " + str(source))

    def beacon(self):
        #self.cbLog("debug", "beacon")
        if self.beaconCalled == 6:
            msg = self.formatRadioMessage(0xBBBB, "beacon", 0)
            self.sendMessage(msg, self.adaptor)
            self.beaconCalled = 0
        else:
            self.beaconCalled += 1
            self.sendQueued()
        reactor.callLater(1, self.beacon)

    def removeNodeMessages(self, nodeID):
        #Remove all queued messages and reference to a node if we get a new include_req
        if nodeID in self.id2addr:
            addr = self.id2addr[nodeID]
            for m in list(self.messageQueue):
                if m["destination"] == addr:
                    self.messageQueue.remove(m)
                    self.cbLog("debug", "removeNodeMessages: " + str(nodeID) + ", removed: " + m["function"])
            del self.id2addr[nodeID]
            del self.addr2id[addr]

    def sendQueued(self):
        now = time.time()
        sentLength = 0
        sentAck = []
        for m in list(self.messageQueue):
            #self.cbLog("debug", "sendQueued: messageQueue: " + str(m["destination"]) + ", " + m["function"] + ", sentAck: " + str(sentAck))
            if sentLength < 120:   # Send max of 120 bytes in a frame
                if m["function"] == "ack":
                    self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]))
                    self.sendMessage(m["message"], self.adaptor)
                    self.messageQueue.remove(m)  # Only send ack once
                    sentAck.append(m["destination"])
                    sentLength += m["message"]["length"]
                elif (m["destination"] not in self.sentTo) and (m["destination"] not in sentAck):
                    self.sendMessage(m["message"], self.adaptor)
                    self.sentTo.append(m["destination"])
                    m["sentTime"] = now
                    m["attempt"] = 1
                    self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]) + ", attempt " + str(m["attempt"]))
                    sentLength += m["message"]["length"]
                elif (now - m["sentTime"] > 9) and (m["destination"] not in sentAck) and (m["attempt"] > 0):
                    if m["attempt"] > 3:
                        self.messageQueue.remove(m)
                        self.sentTo.remove(m["destination"])
                        self.cbLog("debug", "sendQueued: No ack, removed: " + m["function"] + ", for " + str(m["destination"]))
                    else:
                        self.sendMessage(m["message"], self.adaptor)
                        m["sentTime"] = now
                        m["attempt"] += 1
                        self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]) + ", attempt " + str(m["attempt"]))
                        sentLength += m["message"]["length"]
                #self.cbLog("debug", "sendQueued, sentLength: " + str(sentLength))

    def formatRadioMessage(self, destination, function, wakeupInterval, data = None):
        if True:
        #try:
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
            self.cbLog("debug", "Tx: sending: " + str(hexPayload))
            msg= {
                "id": self.id,
                "length": length,
                "request": "command",
                "data": base64.b64encode(m)
            }
            return msg
        #except Exception as ex:
        #    self.cbLog("warning", "Problem formatting message. Exception: " + str(type(ex)) + ", " + str(ex.args))

    def queueRadio(self, msg, destination, function):
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
                                    "interval": 0
                                   }
                                  ]
                      }
                self.sendMessage(req, message["id"])
                self.adaptor = message["id"]
        self.setState("running")
        reactor.callLater(10, self.beacon)

    def onAdaptorData(self, message):
        #self.cbLog("debug", "onAdaptorData, message: " + str(message))
        if message["characteristic"] == "spur":
            self.onRadioMessage(base64.b64decode(message["data"]))

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
        self.readLocalConfig()
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
