#!/usr/bin/env python
# galvanize_app_a.py
"""
Copyright (c) 2015 ContinuumBridge Limited
"""

import sys
import time
import json
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
    "beacon": 0x0A
}
ALERTS = {
    0x0000: "pressed",
    0x0100: "cleared",
    0x0200: "battery"
}
MESSAGE_NAMES = (
    "normalMessage",
    "pressedMessage",
    "overrideMessage",
    "override"
)

Y_STARTS = (
    (32, 0, 0 ,0, 0),
    (16, 50, 0, 0, 0),
    (5, 32, 61, 0, 0),
    (4, 26, 48, 70, 0),
    (0, 20, 40, 60, 80)
);

GALVANIZE_ADDRESS = int(os.getenv('CB_GALVANIZE_ADDRESS', '0x0000'), 16)
CHECK_INTERVAL      = 30
CID                 = "CID157"           # Client ID
GRANT_ADDRESS       = 0xBB00
NORMAL_WAKEUP       = 60                 # How long node should sleep for, seconds/2
config              = {
                        "nodes": [ ]
}

class App(CbApp):
    def __init__(self, argv):
        self.appClass       = "control"
        self.state          = "stopped"
        self.id2addr        = {}          # Node id to node address mapping
        self.addr2id        = {}          # Node address to node if mapping
        self.waiting        = {}          # Messages from client waiting for nodes
        self.maxAddr        = 0
        self.radioOn        = True
        self.beaconTime     = 0
        self.messageQueue   = []
        self.nodeConfig     = {} 

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
            "waiting": self.waiting
        }
        try:
            with open(self.saveFile, 'w') as f:
                json.dump(state, f)
                self.cbLog("debug", "saving state:: " + str(json.dumps(state, indent=4)))
        except Exception as ex:
            self.cbLog("warning", "Problem saving state. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def loadSaved(self):
        try:
            if os.path.isfile(self.saveFile):
                with open(self.saveFile, 'r') as f:
                    state = json.load(f)
                self.cbLog("debug", "Loaded saved state: " + str(json.dumps(state, indent=4)))
                self.id2addr = state["id2addr"]
                self.addr2id = state["addr2id"]
                self.maxAddr = state["maxAddr"]
                self.waiting = state["waiting"]
        except Exception as ex:
            self.cbLog("warning", "Problem loading saved state. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))
        finally:
            try:
                os.remove(self.saveFile)
                self.cbLog("debug", "deleted saved state file")
            except Exception as ex:
                self.cbLog("debug", "Cannot remove saved state file. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

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
        try:
            self.cbLog("debug", "onClientMessage, message: " + str(json.dumps(message, indent=4)))
            if "function" in message:
                if message["function"] == "include_grant":
                    nodeID = message["node"]
                    if nodeID not in self.id2addr:
                        self.maxAddr += 1
                        self.id2addr[nodeID] = self.maxAddr
                        self.cbLog("debug", "id2addr: " + str(self.id2addr))
                        self.addr2id[self.maxAddr] = nodeID
                        self.cbLog("debug", "addr2id: " + str(self.addr2id))
                    data = struct.pack(">IH", nodeID, self.maxAddr)
                    msg = self.formatRadioMessage(GRANT_ADDRESS, "include_grant", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
                    self.queueRadio(msg, self.maxAddr, "include_grant")
                elif message["function"] == "config":
                    self.cbLog("debug", "onClientMessage, id2addr: " + str(self.id2addr))
                    self.cbLog("debug", "onClientMessage, addr2id: " + str(self.addr2id))
                    self.cbLog("debug", "onClientMessage, message[node]: " + str(message["node"]))
                    self.cbLog("debug", "onClientMessage, message[config]: " + str(json.dumps(message["config"], indent=4)))
                    self.nodeConfig[self.id2addr[int(message["node"])]] = message["config"]
                    self.cbLog("debug", "onClentMessage, nodeConfig: " + str(json.dumps(self.nodeConfig, indent=4)))
        except Exception as ex:
            self.cbLog("warning", "onClientMessage exception. Exception. Type: " + str(type(ex)) + "exception: " +  str(ex.args))

    def sendConfig(self, nodeAddr):
        formatMessage = ""
        messageCount = 0
        override = False
        # If there is already config in the message queue for this node, delete it as new config will replace it
        for m in list(self.messageQueue):
            if m["destination"] == nodeAddr:
                del(self.messageQueue[m])
        for m in MESSAGE_NAMES:
            messageCount += 1
            self.cbLog("debug", "in m loop, m: " + m)
            if m == "normalMessage":
                formatMessage = struct.pack("cBcBcB", "S", 4, "R", 0, "F", 2)
            elif m == "pressedMessage":
                formatMessage = struct.pack("cBcBcB", "S", 5, "R", 0, "F", 2)
            elif m == "overrideMessage":
                formatMessage = struct.pack("cBcBcB", "S", 6, "R", 0, "F", 2)
            elif m == "override":
                override = True
                if self.nodeConfig[nodeAddr][m] == True:
                    formatMessage = struct.pack("cB", "C", 1)
                else:
                    formatMessage = struct.pack("cB", "C", 0)
            if not override:
                lines = self.nodeConfig[nodeAddr][m].split("\n")
                numLines = len(lines)
                for l in lines:
                    self.cbLog("debug", "sendConfig, line:: " + str(l))
                    stringLength = len(l) + 1
                    y_start =  Y_STARTS[numLines-1][lines.index(l)]
                    self.cbLog("debug", "sendConfig, y_start: " + str(y_start))
                    formatString = "cBcB" + str(stringLength) + "sc"
                    segment = struct.pack(formatString, "Y", y_start, "C", stringLength, str(l), "\00")
                    formatMessage += segment
                segment = struct.pack("cc", "E", "S") 
                formatMessage += segment
            self.cbLog("debug", "Sending to node: " + str(formatMessage.encode("hex")))
            if messageCount == len(MESSAGE_NAMES):
                wakeup = NORMAL_WAKEUP
            else:
                wakeup = 0
            msg = self.formatRadioMessage(nodeAddr, "config", wakeup, formatMessage)
            self.queueRadio(msg, nodeAddr, "config")
        del(self.nodeConfig[nodeAddr])

    def onRadioMessage(self, message):
        if self.radioOn:
            self.cbLog("debug", "onRadioMessage")
            destination = struct.unpack(">H", message[0:2])[0]
            self.cbLog("debug", "Rx: destination: " + str("{0:#0{1}X}".format(destination,6)))
            if destination == GALVANIZE_ADDRESS:
                source, hexFunction, length = struct.unpack(">HBB", message[2:6])
                try:
                    function = (key for key,value in FUNCTIONS.items() if value==hexFunction).next()
                except:
                    function = "undefined"
                #hexMessage = message.encode("hex")
                #self.cbLog("debug", "hex message after decode: " + str(hexMessage))
                self.cbLog("debug", "source: " + str("{0:#0{1}x}".format(source,6)))
                self.cbLog("debug", "Rx: function: " + function)
                self.cbLog("debug", "Rx: length: " + str(length))

                if function == "include_req":
                    payload = message[10:14]
                    hexPayload = payload.encode("hex")
                    self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    nodeID = struct.unpack(">I", payload)[0]
                    self.cbLog("debug", "onRadioMessage, include_req, nodeID: " + str(nodeID))
                    msg = {
                        "function": "include_req",
                        "include_req": nodeID
                    }
                    self.client.send(msg)
                elif function == "alert":
                    payload = message[10:12]
                    alertType = ALERTS[struct.unpack(">H", payload)[0]]
                    self.cbLog("debug", "onRadioMessage, alert, type: " + str(alertType))
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
                    self.cbLog("debug", "onRadioMessage, woken_up")
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
        wakeup = NORMAL_WAKEUP
        if nodeAddr in self.nodeConfig:
            wakeup = 0;
            reactor.callLater(5, self.sendConfig, nodeAddr)
        else:
            for m in self.messageQueue:
                if m["destination"] == nodeAddr:
                    wakeup = 0;
        return wakeup

    def onAck(self, source):
        self.cbLog("debug", "onAck, source: " + str("{0:#0{1}x}".format(source,6)))
        self.cbLog("debug", "onAck, messageQueue: " + str(json.dumps(self.messageQueue, indent=4)))
        for m in list(self.messageQueue):
            if m["destination"] == source:
                self.cbLog("debug", "onAck, removing message: " + str(m))
                self.messageQueue.remove(m)
                break

    def beacon(self):
        #self.cbLog("debug", "beacon")
        msg = self.formatRadioMessage(0xBBBB, "beacon", 0)
        self.sendMessage(msg, self.adaptor)
        reactor.callLater(2, self.sendQueued)
        reactor.callLater(4, self.beacon)
        self.beaconTime = time.time()

    def sendQueued(self):
        sentTo = []
        now = time.time()
        sentLength = 0
        for m in list(self.messageQueue):
            if sentLength < 240:   # Send max of 240 bytes in a frame
                if m["destination"] not in sentTo:
                    if now - m["sentTime"] > 12:
                        self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]))
                        self.sendMessage(m["message"], self.adaptor)
                        if m["function"] == "ack":  # Only send an ack once
                            self.messageQueue.remove(m)
                        else:
                            m["sentTime"] = now
                            m["attempt"] += 1
                        sentTo.append(m["destination"])
                        sentLength += len(m["message"])
                        self.cbLog("debug", "sendQueued, sentLength: " + str(sentLength))

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
            m += struct.pack(">H", GALVANIZE_ADDRESS)
            if function != "beacon":
                m+= struct.pack("B", FUNCTIONS[function])
                m+= struct.pack("B", length)
                m+= struct.pack("I", timeStamp)
                m+= struct.pack(">H", wakeupInterval)
                self.cbLog("debug", "formatRadioMessage, wakeupInterval: " +  str(wakeupInterval))
            #self.cbLog("debug", "length: " +  str(length))
            if data:
                m += data
            hexPayload = m.encode("hex")
            self.cbLog("debug", "Tx: sending: " + str(hexPayload))
            msg= {
                "id": self.id,
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
            if p["characteristic"] == "galvanize_button":
                req = {"id": self.id,
                       "request": "service",
                       "service": [
                                   {"characteristic": "galvanize_button",
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
        if message["characteristic"] == "galvanize_button":
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
