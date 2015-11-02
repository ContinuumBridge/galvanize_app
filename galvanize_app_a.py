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
        self.devices        = []
        self.idToName       = {} 
        self.id2addr        = {}
        self.addr2id        = {}
        self.maxAddr        = 0

        # Super-class init must be called
        CbApp.__init__(self, argv)

    def setState(self, action):
        self.state = action
        msg = {"id": self.id,
               "status": "state",
               "state": self.state}
        self.sendManagerMessage(msg)

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
                self.addr2id[self.maxAddr] = nodeID
                wakeupInterval = 180
                data = struct.pack("IH", nodeID, self.maxAddr)
                self.sendRadio(GRANT_ADDRESS, "include_grant", 0, data)  # Wakeup = 0 after include_grant (stay awake 10s)
            elif message["function"] == "config":
                nodeConfig = message["config"]
                self.cbLog("debug", "Config for node " + str(message["node"]) + ": " + str(json.dumps(nodeConfig, indent=4)))
                self.cbLog("debug", "m1_l1 " + str(nodeConfig["messages"]["m1"][0]))
                m1_l1 = struct.pack("HHs", 0x11, len(nodeConfig["messages"]["m1"][0]), str(nodeConfig["messages"]["m1"][0]))
                self.cbLog("debug", "sending to: " + message["node"])
                self.sendRadio(self.id2addr[message["node"]], "config", 0, m1_l1)

    def beacon(self):
        message = {
            "id": self.id,
            "request": "command",
            "data": {
                "destination": 0xBBBB,
                "function": "beacon"
            }
        }
        self.sendMessage(message, self.adaptor)
        reactor.callLater(5, self.beacon)

    def onRadioMessage(self, source, function, data):
        self.cbLog("debug", "onRadioMessage, function: " + function)
        if function == "include_req":
            nodeID = struct.unpack("I", data)[0]
            self.cbLog("debug", "onRadioMessage, include_req, nodeID: " + str(nodeID))
            msg = {
                "function": "include_req",
                "include_req": nodeID
            }
            self.client.send(msg)

    def sendRadio(self, destination, function, wakeupInterval, data = None):
        #if self.sending:
        #    self.cbLog("warning", "Could not send " + function + " message because another message is being sent")
        #    return
        msg= {
            "id": self.id,
            "request": "command",
            "data": {
                "destination": destination,
                "function": function,
                "wakeup_interval": wakeupInterval
            }
        }
        if data:
            msg["data"]["data"] = base64.b64encode(data)
        self.sendMessage(msg, self.adaptor)

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
        self.cbLog("debug", "onAdaptorData, message: " + str(message))
        if message["characteristic"] == "galvanize_button":
            self.cbLog("debug", "onAdaptorData, length: " + str(len(base64.b64decode(message["data"]["data"]))))
            self.onRadioMessage(message["data"]["source"], message["data"]["function"], base64.b64decode(message["data"]["data"]))

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
        reactor.callLater(CHECK_INTERVAL, self.checkConnected)
        self.setState("starting")

if __name__ == '__main__':
    App(sys.argv)
