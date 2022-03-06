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
# NAME:	        pdgjson.py ( Python )
#
# COMMENTS:     Job side API, to provide access to work item attributes and data
#               as if the work item was in a Houdini process.
#

from __future__ import print_function, absolute_import

import base64
import json
import gzip
import sys
import os

try:
    import pdgcmd
    from pdgcmd import localizePath
except:
    try:
        import pdgjob.pdgcmd as pdgcmd
        from pdgjob.pdgcmd import localizePath
    except ImportError:
        localizePath = lambda f: f

def getWorkItemJsonPath(item_name):
    try:
        sharedTemp = os.environ['PDG_TEMP']
    except KeyError:
        print("ERROR: $PDG_TEMP must be in environment")
        return None

    json_root = os.path.expandvars(sharedTemp + '/data/' + item_name)
    if os.path.exists(json_root + '.json.gz'):
        return json_root + '.json.gz'
    return json_root + '.json'

def getPyAttribLoader():
    try:
        return os.environ['PDG_PYATTRIB_LOADER']
    except KeyError:
        return ""

def getWorkItemDataSource():
    try:
        return int(os.environ['PDG_ITEM_DATA_SOURCE'])
    except KeyError:
        return 0

def isIterable(obj):
    if sys.version_info.major >= 3:
        import collections
        is_iterable = isinstance(obj, collections.Iterable) \
            and not isinstance(obj, (str, bytes, bytearray))
    else:
        is_iterable = isinstance(obj, (list, tuple))
    return is_iterable

class _enum(object):
    @classmethod
    def _preloadEnums(cls):
        for index in cls._names:
            name = cls._names[index]
            setattr(cls, name, cls(index))

    def __init__(self, value, prefix):
        self._value = value
        if value in self._names:
            self._name = "{}.{}".format(prefix, self._names[value])
        else:
            self._name = "{}.???".format(prefix)

    def __int__(self):
        return self._value

    def __repr__(self):
        return self._name

    def __str__(self):
        return self._name

    def __hash__(self):
        return self._value

    def __eq__(self, other):
        return self._value == int(other)

    def __ne__(self, other):
        return self._value != int(other)

class attribType(_enum):
    _names = {0: "Int", 1: "Float", 2: "String", 3: "File",
              4: "PyObject", 5: "Geometry", 6: "Undefined"}

    def __init__(self, value):
        _enum.__init__(self, value, "attribType")

attribType._preloadEnums()

class attribFlag(_enum):
    _names = {0: "NoFlags", 0xFFFF: "All", 0x0001: "NoCopy", 0x0002: "EnvExport",
              0x0004: "ReadOnly", 0x0008: "Internal", 0x0010: "Operator"}

    def __init__(self, value):
        _enum.__init__(self, value, "attribFlag")

attribFlag._preloadEnums()

class File(object):
    def __init__(self, path="", tag="", modtime=0, owned=False):
        self.path = path
        self.local_path = path
        self.tag = tag
        self.hash = modtime
        self.owned = owned

    def __repr__(self):
        return "<{} tag='{}', owned={} at 0x{:x}>".format(self.path, self.tag,
            self.owned, id(self))

    def asTuple(self):
        return (self.path, self.tag, self.hash, self.owned)

class AttribError(Exception):
    pass

class AttributeBase(object):
    def __init__(self, attrib_type, name, item):
        self._attrib_type = attrib_type
        self._attrib_flags = 0
        self._item = item
        self._name = name
        self._initialized = False

    @property
    def type(self):
        return self._attrib_type

    @property
    def size(self):
        return len(self)

    @property
    def flags(self):
        return self._attrib_flags

    def hasFlag(self, flag):
        int_flag = int(flag)
        return (self._attrib_flags & int_flag) == int_flag

    def hasFlags(self):
        return self._attrib_flags != 0

    def setFlag(self, flag):
        int_flag = int(flag)
        self._attrib_flags |= int_flag

    def setFlags(self, flags):
        self._attrib_flags = flags

    def asNumber(self, index=0):
        return 0

    def asString(self, index=0):
        return ""

class AttributeArray(AttributeBase):
    def __init__(self, attrib_type, data_type, name, item):
        AttributeBase.__init__(self, attrib_type, name, item)
        self._data_type = data_type
        self._data = []

    def __len__(self):
        return len(self._data)

    def __getitem__(self, index):
        return self._data[index]

    def __setitem__(self, index, value):
        self.setValue(value, index)

    def __hash__(self):
        h = 0
        for v in self._data:
            h ^= hash(v)
        return h

    @property
    def values(self):
        return self._data

    def value(self, index=0):
        return self._data[index]

    def setValue(self, value, index=0):
        _value = self._cast(value)

        length = len(self._data)
        if index >= length:
            self._data += [self._data_type()]*(index-length+1)

        self._data[index] = _value
        self._reportValue(_value, index)

    def setValues(self, values):
        self._data = [self._cast(val) for val in values]
        self._reportArray(self._data)

    def truncate(self, length):
        self._data = self._data[0:length]
        self._reportArray(self._data)

    def _cast(self, value):
        return self._data_type(value)

    def _reportValue(self, value, index):
        pass

    def _reportArray(self, values):
        pass

class AttributeInt(AttributeArray):
    def __init__(self, name, item):
        AttributeArray.__init__(self, attribType.Int, int, name, item)

    def _reportValue(self, value, index):
        if not self._initialized:
            return
        pdgcmd.setIntAttrib(self._name, value, index, self._item.reportId,
                            self._item.batchIndex, self._item._server)

    def _reportArray(self, values):
        if not self._initialized:
            return
        pdgcmd.setIntAttribArray(self._name, values, self._item.reportId,
                                 self._item.batchIndex, self._item._server)

class AttributeFloat(AttributeArray):
    def __init__(self, name, item):
        AttributeArray.__init__(self, attribType.Float, float, name, item)

    def _reportValue(self, value, index):
        if not self._initialized:
            return
        pdgcmd.setFloatAttrib(self._name, value, index, self._item.reportId,
                              self._item.batchIndex, self._item._server)

    def _reportArray(self, values):
        if not self._initialized:
            return
        pdgcmd.setFloatAttribArray(self._name, values, self._item.reportId,
                                   self._item.batchIndex, self._item._server)

class AttributeString(AttributeArray):
    def __init__(self, name, item):
        if sys.version_info.major >= 3:
            str_type = str
        else:
            str_type = unicode

        AttributeArray.__init__(self, attribType.String, str_type, name, item)

    def _reportValue(self, value, index):
        if not self._initialized:
            return
        pdgcmd.setStringAttrib(self._name, value, index, self._item.reportId,
                               self._item.batchIndex, self._item._server)

    def _reportArray(self, values):
        if not self._initialized:
            return
        pdgcmd.setStringAttribArray(self._name, values, self._item.reportId,
                                    self._item.batchIndex, self._item._server)

    def _cast(self, value):
        if isinstance(value, self._data_type):
            return value

        try:
            return self._data_type(value, 'utf8')
        except TypeError:
            return AttributeArray._cast(self, value)

class AttributeFile(AttributeArray):
    def __init__(self, name, item):
        AttributeArray.__init__(self, attribType.File, File, name, item)

    def _reportValue(self, value, index):
        if not self._initialized:
            return
        pdgcmd.setFileAttrib(self._name, value.asTuple(), index,
                             self._item.reportId, self._item.batchIndex,
                             self._item._server)

    def _reportArray(self, values):
        if not self._initialized:
            return
        pdgcmd.setFileAttribArray(self._name, [value.asTuple() for value in values],
                                  self._item.reportId, self._item.batchIndex,
                                  self._item._server)

    def setValue(self, value, index=0):
        if not isinstance(value, self._data_type):
            raise AttribError("File attributes must be set to pdg.File values")

        length = len(self._data)
        if index >= length:
            self._data += [self._data_type()]*(index-length+1)

        self._data[index] = value
        self._reportValue(value, index)

    def setValues(self, values):
        for val in values:
            if not isinstance(val, self._data_type):
                raise AttribError("File attributes must be set to pdg.File values")
        self._data = values
        self._reportArray(values)

class AttributePyObject(AttributeBase):
    def __init__(self, name, item):
        AttributeBase.__init__(self, attribType.PyObject, name, item)
        self._object = None

    def __len__(self):
        return 1

    def __hash__(self):
        try:
            return hash(self._object)
        except TypeError:
            return hash(str(self._object))
        return 0
    
    @property
    def object(self):
        return self._object

    @object.setter
    def object(self, obj):
        self._object = obj
        self._reportValue(obj, 0)

    @property
    def values(self):
        return self._object

    def value(self, index=0):
        return self._object

    def setValue(self, value, index=0):
        self._object = value
        self._reportValue(value, 0)

    def _reportValue(self, value, index):
        if not self._initialized:
            return

        loader = getPyAttribLoader()
        if not loader:
            obj_repr = repr(value)
        else:
            mod = __import__(loader)
            obj_repr = base64.encodestring(mod.dumps(value))

        pdgcmd.setPyObjectAttrib(self._name, obj_repr, self._item.reportId,
                                 self._item.batchIndex, self._item._server)

class batchActivation(_enum):
    _names = {0: "First", 1: "Any", 2: "All"}

    def __init__(self, value):
        _enum.__init__(self, value, "batchActivation")

batchActivation._preloadEnums()

class workItemState(_enum):
    _names = {0: "Undefined", 1: "Dirty", 2: "Uncooked", 3: "Waiting",
              4: "Scheduled", 5: "Cooking", 6: "CookedSuccess", 7: "CookedCache",
              8: "CookedFail", 9: "CookedCancel"}

    def __init__(self, value):
        _enum.__init__(self, value, "workItemState")

workItemState._preloadEnums()

class workItemType(_enum):
    _names = {0: "Regular", 1: "Batch", 2: "StaticWrapper", 3: "StaticPartition",
              4: "DynamicPartition"}

    def __init__(self, value):
        _enum.__init__(self, value, "workItemType")

workItemType._preloadEnums()

class workItemExecutionType(_enum):
    _names = {0: "Regular", 1: "LongRunning", 2: "Cleanup"}

    def __init__(self, value):
        _enum.__init__(self, value, "workItemExecutionType")

workItemExecutionType._preloadEnums()

class workItemLogType(_enum):
    _names = {0: "Error", 1: "Warning", 2: "Message", 3: "Raw"}

    def __init__(self, value):
        _enum.__init__(self, value, "workItemLogType")

workItemLogType._preloadEnums()

class WorkItem(object):
    _types = {0: AttributeInt, 1: AttributeFloat, 2: AttributeString,
              3: AttributeFile, 4: AttributePyObject, 5: None,
              6: None}

    @classmethod
    def fromJobEnvironment(cls, item_name=None, cb_server=None, expand_strings=False, as_native=False):
        data_source = getWorkItemDataSource()

        if data_source == 0:
            name = os.environ.get('PDG_ITEM_NAME', item_name)
            if not name:
                print("ERROR: PDG_ITEM_NAME must be in environment or specified via argument flag.")
                exit(1)

            return cls.fromFile(getWorkItemJsonPath(name), cb_server, expand_strings, as_native)
        elif data_source == 1:
            try:
                item_id = int(os.environ['PDG_ITEM_ID'])
            except:
                print("ERROR: PDG_ITEM_ID must be in environment")
                exit(1)

            return cls.fromRPC(item_id, cb_server, expand_strings, as_native)
        else:
            print("ERROR: Unknown value PDG_ITEM_DATA_SOURCE={}".format(data_source))
            exit(1)

    @classmethod
    def fromItemName(cls, item_name, cb_server=None, expand_strings=False, as_native=False):
        item = cls.fromFile(getWorkItemJsonPath(item_name), cb_server, expand_strings, as_native)
        return item

    @classmethod
    def fromRPC(cls, item_id, cb_server=None, expand_strings=False, as_native=False):
        json_data = pdgcmd.getWorkItemJSON(item_id, -1, cb_server)
        return cls.fromString(json_data, cb_server, expand_strings, as_native)

    @classmethod
    def fromFile(cls, json_path, cb_server=None, expand_strings=False, as_native=False):
        extension = os.path.splitext(json_path)
        if len(extension) > 1 and extension[-1] == '.gz':
            with gzip.open(json_path, 'rb') as f:
                json_data = f.read().decode('utf-8')
        else:
            with open(json_path, 'rb') as f:
                json_data = f.read().decode('utf-8')

        if as_native:
            import pdg
            return pdg.WorkItem.loadJSONString(json_data, True)
            
        item_data = json.loads(json_data)
        return cls(item_data, server=cb_server, expand_strings=expand_strings)

    @classmethod
    def fromString(cls, json_data, cb_server=None, expand_strings=False, as_native=False):
        if as_native:
            import pdg
            return pdg.WorkItem.loadJSONString(json_data, True)

        wi = cls(json.loads(json_data), server=cb_server, expand_strings=expand_strings)
        return wi

    def __init__(self, json_obj, batch_parent=None, version=None, server=None, expand_strings=False):
        if version:
            self._version = version
        elif 'version' in json_obj:
            self._version = json_obj['version']
        else:
            self._version = 1

        if 'workitem' in json_obj:
            item_obj = json_obj['workitem']
        else:
            item_obj = json_obj

        if not server:
            self._server = os.environ.get('PDG_RESULT_SERVER', None)
        else:
            self._server = server

        self._nodeName = item_obj['nodeName']

        if self._version < 3:
            self._name = item_obj['name']
        else:
            self._id = item_obj['id']
            self._name = self._nodeName + '_' + str(self._id)

        if self._version < 4:
            self._command = item_obj['command']
        else:
            self._command = ""

        if self._version < 2:
            self._customDataType = ""
            self._customData = ""
        else:
            self._customDataType = item_obj['customDataType']
            self._customData = item_obj['customData']

        self._batchIndex = item_obj['batchIndex']
        self._batchParent = batch_parent
        self._frame = item_obj['frame']
        self._frameStep = item_obj['frameStep']
        self._hasFrame = item_obj['hasFrame']
        self._index = item_obj['index']
        self._isInProcess = item_obj['index']
        self._isStatic = item_obj['isStatic']
        self._priority = item_obj['priority']
        self._state = workItemState(item_obj['state'])
        self._type = workItemType(item_obj['type'])
        self._executionType = workItemType(item_obj['executionType'])

        self._attributes = {}
        self._loadAttribsV2(item_obj, expand_strings)

        if self.type == workItemType.Batch:
            self._batchCount = item_obj['batchCount']
            self._batchOffset = item_obj['batchOffset']
            self._activationMode = batchActivation(int(item_obj['activationMode']))
            self._subItems = [None]*self._batchCount

            if 'subItems' in item_obj:
                self._loadSubItems(item_obj['subItems'])

            if 'batchStart' in item_obj:
                self._batchStart = item_obj['batchStart']
            else:
                self._batchStart = 0
        else:
            self._batchCount = 0
            self._batchOffset = 0
            self._batchStart = 0
            self._activationMode = None
            self._subItems = []

    def __hash__(self):
        return hash(self._nodeName) ^ hash(self._index) ^\
            hash(self._frame) ^ hash(self._priority) ^ self.attribHash(True)

    def attribHash(self, include_internal=True):
        if self.batchParent:
            h = self.batchParent.attribHash(include_internal)
        else:
            h = 0 

        for attrib_name, v in self._attributes.items():
            if not include_internal and v.hasFlag(attribFlag.Internal):
                continue 
            h ^= hash(attrib_name)
            h ^= hash(v)
            h ^= hash(v.flags)
        return h

    def _loadSubItems(self, sub_item_obj):
        for i, sub_item_json in enumerate(sub_item_obj):
            self.subItems[i] = WorkItem(sub_item_json, self, self._version)

    def _loadAttribsV2(self, item_obj, expand_strings):
        attribs = item_obj['attributes']

        for attrib_name in attribs:
            attrib = attribs[attrib_name]

            typenum = attrib['type']
            if typenum is not None:
                attrib_type = attribType(typenum)
                if attrib_type == attribType.Geometry:
                    continue

                new_attr = self.addAttrib(attrib_name, attrib_type, False)
                if attrib_type == attribType.PyObject:
                    try:
                        loader = getPyAttribLoader()
                        if not loader:
                            new_attr.object = eval(attrib['value'])
                        else:
                            mod = __import__(loader)
                            new_attr.object = mod.loads(
                                base64.b64decode(attrib['value']))
                    except:
                        new_attr.object = None
                        print("Python object for attribute '{}' "
                            "failed to deserialize.".format(attrib_name))
                        import traceback
                        traceback.print_exc()
                        continue
                elif attrib_type == attribType.File:
                    new_files = []
                    for val in attrib['value']:
                        f = File()
                        f.path = val['data']
                        f.local_path = localizePath(val['data'])
                        f.tag = val['tag']
                        f.hash = val['hash']
                        f.owned = val['own']
                        f.type = val['type']
                        new_files.append(f)
                    new_attr.setValues(new_files)
                elif attrib_type == attribType.String:
                    if expand_strings:
                        new_attr.setValues([localizePath(val) for val in attrib['value']])
                    else:
                        new_attr.setValues(attrib['value'])
                else:
                    new_attr.setValues(attrib['value'])
                new_attr._initialized = True

    @property
    def customDataType(self):
        return self._customDataType

    @property
    def customData(self):
        return self._customData

    @property
    def activationMode(self):
        return self._activationMode

    @property
    def batchSize(self):
        return self._batchCount

    @property
    def offset(self):
        return self._batchOffset

    @property
    def batchStart(self):
        return self._batchStart

    @property
    def batchIndex(self):
        return self._batchIndex

    @property
    def batchParent(self):
        return self._batchParent

    @property
    def reportName(self):
        # Use of reportName for RPC is deprecated
        if self.batchParent:
            return self.batchParent.name
        return self.name

    @property
    def reportId(self):
        if self.batchParent:
            return self.batchParent._id
        return self._id

    @property
    def command(self):
        if self._version < 4:
            return self._command
        else:
            return self.stringAttribValue('__pdg_commandstring') or ""

    @property
    def frame(self):
        return self._frame

    @property
    def frameStep(self):
        return self._frameStep

    @property
    def hasFrame(self):
        return self._hasFrame

    @property
    def index(self):
        return self._index

    @property
    def isInProcess(self):
        return self._isInProcess

    @property
    def isStatic(self):
        return self._isStatic

    @property
    def name(self):
        return self._name

    @property
    def label(self):
        if self.hasAttribValue('__pdg_label'):
            return self.stringAttribValue('__pdg_label')
        return self.name()

    @property
    def id(self):
        return self._id

    @property
    def priority(self):
        return self._priority

    @property
    def batchItems(self):
        return self._subItems

    @property
    def state(self):
        return self._state

    def setJobState(self, state):
        myid = self.reportId
        if state == workItemState.Cooking:
            pdgcmd.workItemStartCook(myid, self.batchIndex, self._server)
        elif state == workItemState.CookedSuccess:
            pdgcmd.workItemSuccess(myid, self.batchIndex, self._server)
        elif state == workItemState.CookedFail:
            if self.batchIndex > -1:
                raise TypeError("Failing batch item not supported")
            pdgcmd.workItemFailed(myid, self._server)
        else:
            raise TypeError("Job can not be set to {} state".format(state))

    def cookWarning(self, message):
        pdgcmd.warning(message, self.reportId, self._server)

    @property
    def type(self):
        return self._type

    @property
    def executionType(self):
        return self._executionType

    @property
    def subItems(self):
        return self._subItems

    def __getitem__(self, key):
        if not key in self._attributes and self.batchParent:
            return self.batchParent._attributes[key]
        return self._attributes[key]

    def addError(self, message, fail_task=False):
        pdgcmd.printlog(message, prefix='ERROR')
        if fail_task:
            sys.exit(-1)

    def addMessage(self, message):
        pdgcmd.printlog(message)

    def addWarning(self, message):
        pdgcmd.printlog(message, prefix='WARNING')

    def hasAttrib(self, key):
        if not key in self._attributes:
            if self.batchParent:
                return key in self.batchParent._attributes
            return False
        return True

    def attrib(self, key, attrib_type=attribType.Undefined, initialized=True):
        attrib = self._attributes.get(key)
        if attrib is None:
            if self.batchParent and initialized:
                return self.batchParent.attrib(key, attrib_type)
            return None
        if attrib_type == attribType.Undefined:
            return attrib
        if attrib.type == attrib_type:
            return attrib
        return None

    def addAttrib(self, key, attrib_type, initialized=True):
        attrib = self.attrib(key, initialized=initialized)
        if attrib is None:
            int_type = int(attrib_type)
            if int_type not in self._types:
                raise AttribError("Unsupported attribute type")
            if self._types[int_type] is None:
                raise AttribError("Unsupported attribute type")

            attrib = self._types[int_type](key, self)
            attrib._initialized = initialized
            self._attributes[key] = attrib
            return attrib

        if attrib.type != attrib_type:
            raise AttribError("An attribute with the name '{}' already exists\
                with a different type".format(key))
        return attrib

    def attribNames(self, attrib_type=attribType.Undefined):
        names = set()
        if self.batchParent:
            names.update(self.batchParent.attribNames(attrib_type))

        if attrib_type == attribType.Undefined:
            names.update(self._attributes.keys())
        else:
            for attrib in self._attributes:
                if attrib.type == attrib_type:
                    names.update(attrib)
        return list(names)

    def attribValues(self, attrib_type=attribType.Undefined):
        if self.batchParent:
            d = self.batchParent.attribValues(attrib_type)
        else:
            d = {}

        for key in self._attributes:
            if (attrib_type == attribType.Undefined) or \
                    (attrib_type == self._attributes[key].type):
                d[key] = self._attributes[key].values
        return d

    def _attribValue(self, name, index, attrib_type):
        attr = self.attrib(name, attrib_type)
        if attr is None:
            return None
        return attr.value(index)

    def _attribValues(self, name, attrib_type):
        attr = self.attrib(name, attrib_type)
        if attr is None:
            return None
        return attr.values

    def _setAttrib(self, name, index, value, attrib_type):
        attr = self.attrib(name, attrib_type)
        if attr is None:
            return False
        attr.setValue(value, index)
        return True

    def _setAttribArray(self, name, value, attrib_type):
        attr = self.attrib(name, attrib_type)
        if attr is None:
            return False
        attr.setValues(value)
        return True

    def attribValue(self, name, index=0):
        return self._attribValue(name, index, attribType.Undefined)

    def intAttribValue(self, name, index=0):
        return self._attribValue(name, index, attribType.Int)

    def floatAttribValue(self, name, index=0):
        return self._attribValue(name, index, attribType.Float)

    def stringAttribValue(self, name, index=0):
        return self._attribValue(name, index, attribType.String)

    def fileAttribValue(self, name, index=0):
        return self._attribValue(name, index, attribType.File)

    def pyObjectAttribValue(self, name):
        return self._attribValue(name, 0, attribType.PyObject)

    def attribArray(self, name):
        return self._attribValues(name, attribType.Undefined)

    def intAttribArray(self, name):
        return self._attribValues(name, attribType.Int)

    def floatAttribArray(self, name):
        return self._attribValues(name, attribType.Float)

    def stringAttribArray(self, name):
        return self._attribValues(name, attribType.String)

    def fileAttribArray(self, name):
        return self._attribValues(name, attribType.File)

    @property
    def inputResultData(self):
        return self.fileAttribArray("__pdg_inputfiles")

    def inputResultDataForTag(self, tag):
        if not self.inputResultData:
            return []
        return [f for f in self.inputResultData if f.tag.startswith(tag)]

    def setIntAttrib(self, name, value, index=0):
        self.addAttrib(name, attribType.Int)

        if isIterable(value):
            return self._setAttribArray(name, value, attribType.Int)

        return self._setAttrib(name, index, value, attribType.Int)

    def setFloatAttrib(self, name, value, index=0):
        self.addAttrib(name, attribType.Float)

        if isIterable(value):
            return self._setAttribArray(name, value, attribType.Float)

        return self._setAttrib(name, index, value, attribType.Float)

    def setStringAttrib(self, name, value, index=0):
        self.addAttrib(name, attribType.String)

        if isIterable(value):
            return self._setAttribArray(name, value, attribType.String)

        return self._setAttrib(name, index, value, attribType.String)

    def setFileAttrib(self, name, value, index=0):
        self.addAttrib(name, attribType.File)

        if isIterable(value):
            return self._setAttribArray(name, value, attribType.File)

        return self._setAttrib(name, index, value, attribType.File)

    def setPyObjectAttrib(self, name, value, index=0):
        self.addAttrib(name, attribType.PyObject)
        return self._setAttrib(name, index, value, attribType.PyObject)

    def addResultData(self, path, tag="", hash_code=0, own=False):
        pdgcmd.reportResultData(
            path,
            workitem_id=self.reportId,
            subindex=self.batchIndex,
            result_data_tag=tag,
            hash_code=hash_code,
            server_addr=self._server)

    def invalidateCache(self):
        pdgcmd.invalidateCache(
            workitem_id=self.reportId,
            subindex=self.batchIndex,
            server_addr=self._server)

def strData(item, field, index=0):
    """
    Returns string data with the given name and index.
    Raises ValueError if not found.
    item        item to search
    field       data field name
    index       index of data in string array
    """
    if item is None:
        raise TypeError("item is None")
    val = item.stringAttribValue(field, index)
    if val is None:
        val = item.fileAttribValue(field, index)
        if val is not None:
            return val.local_path

        if not hasStrData(item, field):
            raise ValueError("String data '{}' not found on {}".format(field, item.name))
        else:
            raise IndexError("Invalid index {} for string data '{}'".format(index, field))
    return val

def strDataArray(item, field):
    """
    Returns string data array for the given name.
    Raises ValueError if not found.
    item        item to search
    field       data field name
    """
    if item is None:
        raise TypeError("item is None")
    val = item.stringAttribArray(field)
    if val is None:
        val = item.fileAttribValue(field)
        if val is not None:
            return [v.path for v in val]

        raise ValueError("String data '{}' not found on {}".format(field, item.name))
    return val

def hasStrData(item, field, index=0):
    """
    Returns True if the item has string data with the given name
    and the given index.
    item        item to search
    field       string tag for data
    index       index of data in int array
    """
    if item is None:
        raise TypeError("item is None")
    return item.stringAttribValue(field, index) is not None

def intData(item, field, index=0):
    """
    Returns int data with the given tag and index.
    Raises ValueError if not found.
    item        item to search
    field       string tag for data
    index       index of data in int array
    """
    if item is None:
        raise TypeError("item is None")
    val = item.intAttribValue(field, index)
    if val is None:
        if not hasIntData(item, field):
            raise ValueError("Int data '{}' not found on {}".format(field, item.name))
        else:
            raise IndexError("Invalid index {} for int data '{}'".format(index, field))
    return val

def intDataArray(item, field):
    """
    Returns int data array for the given tag.
    Raises ValueError if not found.
    item        item to search
    field       string tag for data
    """
    if item is None:
        raise TypeError("item is None")
    val = item.intAttribArray(field)
    if val is None:
        raise ValueError("Int data '{}' not found on {}".format(field, item.name))
    return val

def hasIntData(item, field, index=0):
    """
    Returns True if the item has int data with the given name
    and the given index.
    item        item to search
    field       string tag for data
    index       index of data in int array
    """
    if item is None:
        raise TypeError("item is None")
    return item.intAttribValue(field, index) is not None

def floatData(item, field, index=0):
    """
    Returns float data with the given tag and index.
    Raises ValueError if not found.
    item        item to search
    field       string tag for data
    index       index of data in float array
    """
    if item is None:
        raise TypeError("item is None")
    val = item.floatAttribValue(field, index)
    if val is None:
        if not hasFloatData(item, field):
            raise ValueError("Float data '{}' not found on {}".format(field, item.name))
        else:
            raise IndexError("Invalid index {} for float data '{}'".format(index, field))
    return val

def floatDataArray(item, field):
    """
    Returns float data array for the given tag.
    Raises ValueError if not found.
    item        item to search
    field       string tag for data
    """
    if item is None:
        raise TypeError("item is None")
    val = item.floatAttribArray(field)
    if val is None:
        raise ValueError("Float data '{}' not found on {}".format(field, item.name))
    return val

def hasFloatData(item, field, index=0):
    """
    Returns True if the item has float data with the given name
    and the given index.
    item        item to search
    field       string tag for data
    index       index of data in int array
    """
    if item is None:
        raise TypeError("item is None")
    return item.floatAttribValue(field, index) is not None
