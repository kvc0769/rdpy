#
# Copyright (c) 2014 Sylvain Peyrefitte
#
# This file is part of rdpy.
#
# rdpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
Implement Multi-Channel Service

Each channel have a particular role.
The main channel is the graphical channel.
It exist channel for file system order, audio channel, clipboard etc...
"""
from rdpy.network.layer import LayerAutomata, StreamSender, Layer, LayerMode
from rdpy.network.type import sizeof, Stream, UInt8, UInt16Le
from rdpy.network.error import InvalidExpectedDataException, InvalidValue, InvalidSize
from rdpy.protocol.rdp.ber import writeLength

import ber, gcc, per

class Message(object):
    """
    Message type
    """
    MCS_TYPE_CONNECT_INITIAL = 0x65
    MCS_TYPE_CONNECT_RESPONSE = 0x66

class DomainMCSPDU:
    """
    Domain MCS PDU header
    """
    ERECT_DOMAIN_REQUEST = 1
    DISCONNECT_PROVIDER_ULTIMATUM = 8
    ATTACH_USER_REQUEST = 10
    ATTACH_USER_CONFIRM = 11
    CHANNEL_JOIN_REQUEST = 14
    CHANNEL_JOIN_CONFIRM = 15
    SEND_DATA_REQUEST = 25
    SEND_DATA_INDICATION = 26

class Channel:
    """
    Channel id of main channels use in RDP
    """
    MCS_GLOBAL_CHANNEL = 1003
    MCS_USERCHANNEL_BASE = 1001

class MCS(LayerAutomata):
    """
    Multiple Channel Service layer
    the main layer of RDP protocol
    is why he can do everything and more!
    """
    class MCSProxySender(Layer, StreamSender):
        """
        Proxy use to set as transport layer for upper channel
        use to abstract channel id for presentation layer
        """
        def __init__(self, mcs, channelId):
            """
            @param mcs: MCS layer use as proxy
            @param channelId: channel id for presentation layer 
            """
            self._mcs = mcs
            self._channelId = channelId
            
        def send(self, data):
            """
            A send proxy function, use channel id and specific 
            send function of MCS layer
            """
            self._mcs.send(self._channelId, data)
            
        def close(self):
            """
            Close wrapped layer
            """
            self._mcs.close()
            
        def getUserId(self):
            """
            @return: mcs user id
            """
            return self._mcs._userId
        
        def getChannelId(self):
            """
            @return: return channel id of proxy
            """
            return self._channelId
            
        def getGCCClientSettings(self):
            """
            @return: mcs layer gcc client settings
            """
            return self._mcs._clientSettings
        
        def getGCCServerSettings(self):
            """
            @return: mcs layer gcc server settings
            """
            return self._mcs._serverSettings
        
    
    def __init__(self, mode, presentation):
        """
        @param mode: mode of MCS layer
        @param presentation: presentation layer
        """
        LayerAutomata.__init__(self, mode, presentation)
        self._clientSettings = gcc.clientSettings()
        self._serverSettings = gcc.serverSettings()
        #default user Id
        self._userId = 1 + Channel.MCS_USERCHANNEL_BASE
        #list of channel use in this layer and connection state
        self._channelIds = {Channel.MCS_GLOBAL_CHANNEL: presentation}
        #use to record already requested channel
        self._channelIdsRequested = {}
    
    def connect(self):
        """
        Connection send for client mode
        a write connect initial packet
        """
        if self._mode == LayerMode.CLIENT:
            self._clientSettings.getBlock(gcc.MessageType.CS_CORE).serverSelectedProtocol.value = self._transport._selectedProtocol
            self.sendConnectInitial()
        else:
            self.setNextState(self.recvConnectInitial)
        
    def connectNextChannel(self):
        """
        Send sendChannelJoinRequest message on next disconnect channel
        client automata function
        """
        for (channelId, layer) in self._channelIds.iteritems():
            #for each disconnect channel send a request
            if not self._channelIdsRequested.has_key(channelId):
                self.sendChannelJoinRequest(channelId)
                self.setNextState(self.recvChannelJoinConfirm)
                return
            
        #connection is done
        self.setNextState(self.recvData)
        #try connection on all requested channel
        for (channelId, layer) in self._channelIds.iteritems():
            if self._channelIdsRequested[channelId] and not layer is None:
                #use proxy for each channel
                layer._transport = MCS.MCSProxySender(self, channelId)
                layer.connect()
                
    def sendConnectInitial(self):
        """
        Send connect initial packet
        client automata function
        """
        ccReq = gcc.writeConferenceCreateRequest(self._clientSettings)
        ccReqStream = Stream()
        ccReqStream.writeType(ccReq)
        
        tmp = (ber.writeOctetstring("\x01"), ber.writeOctetstring("\x01"), ber.writeBoolean(True),
               self.writeDomainParams(34, 2, 0, 0xffff),
               self.writeDomainParams(1, 1, 1, 0x420),
               self.writeDomainParams(0xffff, 0xfc17, 0xffff, 0xffff),
               ber.writeOctetstring(ccReqStream.getvalue()))
        self._transport.send((ber.writeApplicationTag(Message.MCS_TYPE_CONNECT_INITIAL, sizeof(tmp)), tmp))
        #we must receive a connect response
        self.setNextState(self.recvConnectResponse)
        
    def sendConnectResponse(self):
        """
        Send connect response
        server automata function
        """
        ccReq = gcc.writeConferenceCreateResponse(self._serverSettings)
        ccReqStream = Stream()
        ccReqStream.writeType(ccReq)
        
        tmp = (ber.writeEnumerated(0), ber.writeInteger(0), self.writeDomainParams(22, 3, 0, 0xfff8), 
               ber.writeOctetstring(ccReqStream.getvalue()))
        self._transport.send((ber.writeApplicationTag(Message.MCS_TYPE_CONNECT_RESPONSE, sizeof(tmp)), tmp))
        
        self.setNextState(self.recvErectDomainRequest)
        
    def sendErectDomainRequest(self):
        """
        Send a formated erect domain request for RDP connection
        client automata function
        """
        self._transport.send((self.writeMCSPDUHeader(UInt8(DomainMCSPDU.ERECT_DOMAIN_REQUEST)), 
                              per.writeInteger(0), 
                              per.writeInteger(0)))
        
    def sendAttachUserRequest(self):
        """
        Send a formated attach user request for RDP connection
        client automata function
        """
        self._transport.send(self.writeMCSPDUHeader(UInt8(DomainMCSPDU.ATTACH_USER_REQUEST)))
        
    def sendAttachUserConfirm(self):
        """
        Send attach user confirm
        server automata function
        """
        self._transport.send((self.writeMCSPDUHeader(UInt8(DomainMCSPDU.ATTACH_USER_CONFIRM)), 
                             per.writeEnumerates(0), 
                             per.writeInteger16(self._userId, Channel.MCS_USERCHANNEL_BASE)))
        
    def sendChannelJoinRequest(self, channelId):
        """
        Send a formated Channel join request from client to server
        client automata function
        @param channelId: id of channel requested
        """
        self._transport.send((self.writeMCSPDUHeader(UInt8(DomainMCSPDU.CHANNEL_JOIN_REQUEST)), 
                              per.writeInteger16(self._userId, Channel.MCS_USERCHANNEL_BASE), 
                              per.writeInteger16(channelId)))
        
    def sendChannelJoinConfirm(self, channelId, confirm):
        """
        Send a confirm channel (or not) to client
        @param channelId: id of channel
        @param confirm: connection state 
        """
        self._transport.send((self.writeMCSPDUHeader(UInt8(DomainMCSPDU.CHANNEL_JOIN_CONFIRM)), 
                              per.writeEnumerates(int(confirm)), 
                              per.writeInteger16(self._userId, Channel.MCS_USERCHANNEL_BASE), 
                              per.writeInteger16(channelId), 
                              per.writeInteger16(channelId)))
    
    def send(self, channelId, data):
        """
        Specific send function for channelId
        @param channelId: Channel use to send
        @param data: message to send
        """
        self._transport.send((self.writeMCSPDUHeader(UInt8(DomainMCSPDU.SEND_DATA_REQUEST)), 
                              per.writeInteger16(self._userId, Channel.MCS_USERCHANNEL_BASE), 
                              per.writeInteger16(channelId), 
                              UInt8(0x70), 
                              per.writeLength(sizeof(data)), data))
    
    def recvConnectInitial(self, data):
        """
        Receive MCS connect initial from client
        server automata function
        @param data: Stream
        """
        ber.readApplicationTag(data, UInt8(Message.MCS_TYPE_CONNECT_INITIAL))
        ber.readOctetString(data)
        ber.readOctetString(data)
        
        if not ber.readBoolean(data):
            raise InvalidExpectedDataException("invalid expected BER boolean tag")
        
        self.readDomainParams(data)
        self.readDomainParams(data)
        self.readDomainParams(data)
        self._clientSettings = gcc.readConferenceCreateRequest(Stream(ber.readOctetString(data)))
        
        self._serverSettings.getBlock(gcc.MessageType.SC_NET).channelIdArray._array = [UInt16Le(x + Channel.MCS_GLOBAL_CHANNEL) for x in range(1, len(self._clientSettings.getBlock(gcc.MessageType.CS_NET).channelDefArray._array) + 1)]
        
        self.sendConnectResponse()
    
    def recvConnectResponse(self, data):
        """
        Receive MCS connect response from server
        client automata function
        @param data: Stream
        """
        ber.readApplicationTag(data, UInt8(Message.MCS_TYPE_CONNECT_RESPONSE))
        ber.readEnumerated(data)
        ber.readInteger(data)
        self.readDomainParams(data)
        if not ber.readUniversalTag(data, ber.Tag.BER_TAG_OCTET_STRING, False):
            raise InvalidExpectedDataException("invalid expected BER tag")
        gccRequestLength = ber.readLength(data)
        if data.dataLen() != gccRequestLength:
            raise InvalidSize("bad size of GCC request")
        self._serverSettings = gcc.readConferenceCreateResponse(data)
        
        #send domain request
        self.sendErectDomainRequest()
        #send attach user request
        self.sendAttachUserRequest()
        #now wait user confirm from server
        self.setNextState(self.recvAttachUserConfirm)
        
    def recvErectDomainRequest(self, data):
        """
        Receive erect domain request
        server automata function
        @param data: Stream
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.ERECT_DOMAIN_REQUEST):
            raise InvalidExpectedDataException("Invalid MCS PDU : ERECT_DOMAIN_REQUEST expected")
        
        per.readInteger(data)
        per.readInteger(data)
        
        self.setNextState(self.recvAttachUserRequest)
        
    def recvAttachUserRequest(self, data):
        """
        Receive Attach user request
        server automata function
        @param data: Stream
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.ATTACH_USER_REQUEST):
            raise InvalidExpectedDataException("Invalid MCS PDU : ATTACH_USER_REQUEST expected")
        
        self.sendAttachUserConfirm()
        self.setNextState(self.recvChannelJoinRequest)
        
    def recvAttachUserConfirm(self, data):
        """
        Receive an attach user confirm
        client automata function
        @param data: Stream
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.ATTACH_USER_CONFIRM):
            raise InvalidExpectedDataException("Invalid MCS PDU : ATTACH_USER_CONFIRM expected")
        
        if per.readEnumerates(data) != 0:
            raise InvalidExpectedDataException("Server reject user")
        
        self._userId = per.readInteger16(data, Channel.MCS_USERCHANNEL_BASE)
            
        #build channel list because we have user id
        #add default channel + channels accepted by GCC connection sequence
        self._channelIds[self._userId] = None
        
        self.connectNextChannel()
        
    def recvChannelJoinRequest(self, data):
        """
        Receive for each client channel a request
        server automata function
        @param data: Stream
        
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.CHANNEL_JOIN_REQUEST):
            raise InvalidExpectedDataException("Invalid MCS PDU : CHANNEL_JOIN_REQUEST expected")
        
        userId = per.readInteger16(data, Channel.MCS_USERCHANNEL_BASE)
        if self._userId != userId:
            raise InvalidExpectedDataException("Invalid MCS User Id")
        
        channelId = per.readInteger16(data)
        self.sendChannelJoinConfirm(channelId, channelId in self._channelIds.keys() or channelId == self._userId)
    
    def recvChannelJoinConfirm(self, data):
        """
        Receive a channel join confirm from server
        client automata function
        @param data: Stream
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.CHANNEL_JOIN_CONFIRM):
            raise InvalidExpectedDataException("Invalid MCS PDU : CHANNEL_JOIN_CONFIRM expected")
        
        confirm = per.readEnumerates(data)
        
        userId = per.readInteger16(data, Channel.MCS_USERCHANNEL_BASE)
        if self._userId != userId:
            raise InvalidExpectedDataException("Invalid MCS User Id")
        
        channelId = per.readInteger16(data)
        #save state of channel
        self._channelIdsRequested[channelId] = (confirm == 0)
        self.connectNextChannel()
        
    def recvData(self, data):
        """
        Main receive method
        @param data: Stream 
        """
        opcode = UInt8()
        data.readType(opcode)
        
        if self.readMCSPDUHeader(opcode.value, DomainMCSPDU.DISCONNECT_PROVIDER_ULTIMATUM):
            print "INFO : MCS DISCONNECT_PROVIDER_ULTIMATUM"
            self._transport.close()
            return
            
        elif not self.readMCSPDUHeader(opcode.value, DomainMCSPDU.SEND_DATA_INDICATION):
            raise InvalidExpectedDataException("Invalid expected MCS opcode")
        
        #server user id
        per.readInteger16(data, Channel.MCS_USERCHANNEL_BASE)
        
        channelId = per.readInteger16(data)
        
        per.readEnumerates(data)       
        per.readLength(data)
        
        #channel id doesn't match a requested layer
        if not self._channelIdsRequested.has_key(channelId):
            print "ERROR : receive data for an unrequested layer"
            return
        
        #channel id math an unconnected layer
        if not self._channelIdsRequested[channelId]:
            print "ERROR : receive data for an unconnected layer"
            return
        
        self._channelIds[channelId].recv(data) 
    
    def writeDomainParams(self, maxChannels, maxUsers, maxTokens, maxPduSize):
        """
        Write a special domain parameter structure
        use in connection sequence
        @param maxChannels: number of MCS channel use
        @param maxUsers: number of MCS user used (1)
        @param maxTokens: unknown
        @param maxPduSize: unknown
        @return: domain parameter structure
        """
        domainParam = (ber.writeInteger(maxChannels), ber.writeInteger(maxUsers), ber.writeInteger(maxTokens),
                       ber.writeInteger(1), ber.writeInteger(0), ber.writeInteger(1),
                       ber.writeInteger(maxPduSize), ber.writeInteger(2))
        return (ber.writeUniversalTag(ber.Tag.BER_TAG_SEQUENCE, True), writeLength(sizeof(domainParam)), domainParam)
    
    def writeMCSPDUHeader(self, mcsPdu, options = 0):
        """
        Write MCS PDU header
        @param mcsPdu: PDU code
        @param options: option contains in header
        @return: UInt8
        """
        return (mcsPdu << 2) | options
    
    def readMCSPDUHeader(self, opcode, mcsPdu):
        """
        Read mcsPdu header and return options parameter
        @param opcode: opcode
        @param mcsPdu: mcsPdu will be checked
        @return: true if opcode is correct
        """
        return (opcode >> 2) == mcsPdu
    
    def readDomainParams(self, s):
        """
        Read domain parameters structure
        @return: (max_channels, max_users, max_tokens, max_pdu_size)
        """
        if not ber.readUniversalTag(s, ber.Tag.BER_TAG_SEQUENCE, True):
            raise InvalidValue("bad BER tags")
        ber.readLength(s)#length
        max_channels = ber.readInteger(s)
        max_users = ber.readInteger(s)
        max_tokens = ber.readInteger(s)
        ber.readInteger(s)
        ber.readInteger(s)
        ber.readInteger(s)
        max_pdu_size = ber.readInteger(s)
        ber.readInteger(s)
        return (max_channels, max_users, max_tokens, max_pdu_size)
        