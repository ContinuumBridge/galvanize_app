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
    "overrideMessage"
)

GALVANIZE_ADDRESS = int(os.getenv('CB_GALVANIZE_ADDRESS', '0x0000'), 16)
CHECK_INTERVAL      = 30
CID                 = "CID157"  # Client ID
GRANT_ADDRESS       = 0xBB00
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
        self.cbLog("debug", "onClientMessage, message: " + str(json.dumps(message, indent=4)))
        if "function" in message:
            if message["function"] == "include_grant":
                nodeID = message["node"]
                if nodeID in self.id2addr:
                    del(self.id2addr[nodeID])
                self.maxAddr += 1
                self.id2addr[nodeID] = self.maxAddr
                self.cbLog("debug", "id2addr: " + str(self.id2addr))
                self.addr2id[self.maxAddr] = nodeID
                self.cbLog("debug", "addr2id: " + str(self.addr2id))
                wakeupInterval = 180
                data = struct.pack(">IH", nodeID, self.maxAddr)
                msg = self.formatRadioMessage(GRANT_ADDRESS, "include_grant", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
                self.queueRadio(msg, self.maxAddr, "include_grant")
            elif message["function"] == "config":
                nodeConfig = message["config"]
                self.cbLog("debug", "Config for node " + str(message["node"]) + ": " + str(json.dumps(nodeConfig, indent=4)))
                self.cbLog("debug", "m1: " + str(nodeConfig["normalMessage"]))
                for m in MESSAGE_NAMES: 
                    lines = nodeConfig[m].split("\n")
                    for l in lines:
                        stringLength = len(l)
                        code = (MESSAGE_NAMES.index(m)+1)<<4 | (lines.index(l)+1)
                        self.cbLog("debug", "onClientMessage, line: " + l + ", code: " + str(hex(code)))
                        formatString = "BB" + str(stringLength) + "s"
                        formatMessage = struct.pack(formatString, code, stringLength, str(l))
                        self.cbLog("debug", "Sending to node: " + str(formatMessage.encode("hex")))
                        msg = self.formatRadioMessage(self.id2addr[message["node"]], "config", 0, formatMessage)
                        self.queueRadio(msg, self.id2addr[message["node"]], "config")
                    code = 0xf0 | (MESSAGE_NAMES.index(m)+1)
                    fontAndNumber = 0x10 | (len(lines))
                    formatMessage = struct.pack("BB", code, fontAndNumber)
                    self.cbLog("debug", "Sending to node: " + str(formatMessage.encode("hex")))
                    msg = self.formatRadioMessage(self.id2addr[message["node"]], "config", 0, formatMessage)
                    self.queueRadio(msg, self.id2addr[message["node"]], "config")

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
                    payload = message[6:10]
                    hexPayload = payload.encode("hex")
                    self.cbLog("debug", "Rx: hexPayload: " + str(hexPayload) + ", length: " + str(len(payload)))
                    nodeID = struct.unpack("I", payload)[0]
                    self.cbLog("debug", "onRadioMessage, include_req, nodeID: " + str(nodeID))
                    msg = {
                        "function": "include_req",
                        "include_req": nodeID
                    }
                    self.client.send(msg)
                elif function == "alert":
                    payload = message[6:8]
                    alertType = ALERTS[struct.unpack(">H", payload)[0]]
                    self.cbLog("debug", "onRadioMessage, alert, type: " + str(alertType))
                    msg = {
                        "function": "alert",
                        "type": alertType,
                        "source": self.addr2id[source]
                    }
                    self.client.send(msg)
                    msg = self.formatRadioMessage(source, "ack", 0)
                    self.queueRadio(msg, source, "ack")
                elif function == "woken_up":
                    self.cbLog("debug", "onRadioMessage, woken_up")
                    msg = self.formatRadioMessage(source, "ack", 60)
                    self.queueRadio(msg, source, "ack")
                elif function == "ack":
                    self.onAck(source)
                else:
                    self.cbLog("warning", "onRadioMessage, undefined message, source " + str(source) + ", function: " + function)

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
        reactor.callLater(3, self.sendQueued)
        reactor.callLater(5, self.beacon)
        self.beaconTime = time.time()

    def sendQueued(self):
        sentTo = []
        now = time.time()
        for m in list(self.messageQueue):
            if m["destination"] not in sentTo:
                if now - m["sentTime"] > 15:
                    self.cbLog("debug", "sendQueued: Tx: " + m["function"] + " to " + str(m["destination"]))
                    self.sendMessage(m["message"], self.adaptor)
                    if m["function"] == "ack":  # Only send an ack once
                        self.messageQueue.remove(m)
                    else:
                        m["sentTime"] = now
                        m["attempt"] += 1
                    sentTo.append(m["destination"])

    def formatRadioMessage(self, destination, function, wakeupInterval, data = None):
        if True:
        #try:
            length = 6
            if function != "beacon":
                length += 2
            if data:
                length += len(data)
                #self.cbLog("debug", "data length: " + str(length))
            m = ""
            m += struct.pack(">H", destination)
            m += struct.pack(">H", GALVANIZE_ADDRESS)
            m+= struct.pack("B", FUNCTIONS[function])
            m+= struct.pack("B", length)
            if function != "beacon":
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
