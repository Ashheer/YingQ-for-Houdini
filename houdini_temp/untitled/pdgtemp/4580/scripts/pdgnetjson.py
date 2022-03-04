#
# Copyright (c) <2020> Side Effects Software Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# NAME:         pdgnetjson.py ( Python )
#
# COMMENTS:     Utility methods for pdgnet RPC json encoding/decoding.
#

import json
import base64

# For handling xmlrpclib.Binary data
try:
    import xmlrpclib # python 2.x
except:
    import xmlrpc.client as xmlrpclib

class PDGNetRPCJsonEncoder(json.JSONEncoder):
    """ Json Encoder for PDGNet RPC """

    def default(self, obj):
        if isinstance(obj, bytes):
            data = base64.b64encode(obj).decode("UTF-8")
            return {"_bytes":data}
        elif isinstance(obj, xmlrpclib.Binary):
            # xmlrpclib.Binary.data is 8-bit string representation of bytes
            data = base64.b64encode(obj.data).decode("UTF-8")
            return {"_xmlrpclibBinary":data}

        # Try to serialize custom class
        try:
            return {"_klass":{"name":obj.__class__.__name__, "dict":obj.__dict__}}
        except:
            # Let the base class default method raise the TypeError
            return json.JSONEncoder.default(self, obj)


class PDGNetRPCJsonDecoder(json.JSONDecoder):
    """ Json Decoder for PDGNet RPC """

    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        if "_bytes" in obj:
            return base64.b64decode(obj["_bytes"].encode("UTF-8"))
        elif "_xmlrpclibBinary" in obj:
            # Convert from 8-bit string into xmlrpclib.Binary
            data = base64.b64decode(obj["_xmlrpclibBinary"].encode("UTF-8"))
            return xmlrpclib.Binary(data)
        elif "_klass"in obj:
            klass = globals()[obj["_klass"]["name"]]
            klass_dict = obj["_klass"]["dict"]
            return klass(**klass_dict)
        return obj


class PDGNetRPCJsonWrapper(object):
    """ Wrapper for Json encoding and decoding of RPC method and arguments """

    def __init__(self, encoder=PDGNetRPCJsonEncoder, decoder=PDGNetRPCJsonDecoder):
        self.encoder = encoder
        self.decoder = decoder

    def rpcToDict(self, func_name, *args):
        return {"func":func_name, "args":args}

    def dumps(self, object_):
        return json.dumps(object_, cls=self.encoder)

    def loads(self, str_):
        return json.loads(str_, cls=self.decoder)

    def rpcToString(self, func_name, *args):
        return self.dumps(self.rpcToDict(func_name, *args))

    def stringToJson(self, json_str):
        return self.loads(json_str)

    def rpcReplyToString(self, reply_data):
        return json.dumps({"reply":reply_data}, cls=self.encoder)
