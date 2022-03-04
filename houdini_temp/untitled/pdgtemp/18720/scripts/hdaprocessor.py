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
# NAME:	        hdaprocessor.py ( Python )
#
# COMMENTS:     Wrapper script for cooking HDAs
#

from __future__ import print_function, absolute_import

import os
import argparse
import sys
import traceback

from multiprocessing.connection import Client
from pdgutils import PDGNetMQRelay
from pdgutils import mqSetLogLevel, PDGNetLogLevel

import hou
import pdg

from pdg import strDataArray, intDataArray, floatDataArray
from pdg import hasStrData

from six.moves import range

try:
    from pdgcmd import reportResultData, makeDirSafe
    from pdgcmd import localizePath, delocalizePath
    from pdgcmd import waitUntilReady, workItemSuccess, workItemStartCook
    from pdgcmd import getWorkItemJSON
    from pdgcmd import RedirectBuffer, printlog

    from pdgjson import getWorkItemJsonPath
    from pdgnetrpc import PDGNetRPCMessage
except ImportError:
    from pdgjob.pdgcmd import reportResultData, makeDirSafe
    from pdgjob.pdgcmd import localizePath, delocalizePath
    from pdgjob.pdgcmd import waitUntilReady, workItemSuccess, workItemStartCook
    from pdgjob.pdgcmd import getWorkItemJSON
    from pdgjob.pdgcmd import RedirectBuffer, printlog

    from pdgjob.pdgjson import getWorkItemJsonPath
    from pdgjob.pdgnetrpc import PDGNetRPCMessage

BATCH_MODE_OFF = 0
BATCH_MODE_ALL = 1

ALL_FRAMES = 0
FIRST_FRAME = 1

ON_MISSING_ERROR = 0
ON_MISSING_WARN = 1
ON_MISSING_IGNORE = 2

def getInputFilePath(inputfile, temp_dir):
    hdapath = ''
    try:
        hdapath = hou.findFile(inputfile)
    except hou.OperationFailed:
        try:
            # It may be an expanded hda
            hdapath = hou.findDirectory(inputfile)
        except hou.OperationFailed:
            pass

    # If we still didn't find it, check the temp dir
    if not hdapath:
        temp_dir_files = os.listdir(temp_dir)
        for f in temp_dir_files:
            if f == inputfile:
                hdapath = temp_dir + '/' + f
                break

    return hdapath

def getSupportedHdaTypes():
    supported = ['Auto', 'Sop', 'Object', 'Cop2', 'Lop']
    return supported

def installHda(hda_path):
    hou.hda.installFile(hda_path)

def getHdaDefinition(hda_path, optype):
    definitions = hou.hda.definitionsInFile(hda_path)

    for definition in definitions:
        # If operator type was not specified, return the first op
        if not optype:
            return definition

        optype_name = definition.nodeTypeCategory().name() + "/" + definition.nodeTypeName()
        if optype_name == optype:
            return definition

    return None

def getAssetDefinitionCategoryName(definition):
    return definition.nodeTypeCategory().name()

def getNumOfInputs(definition):
    return definition.maxNumInputs()

def getNumOfOutputs(definition):
    return definition.nodeType().maxNumOutputs()

def createNode(location, node_name):
    container_node = hou.node(location)
    if not container_node:
        return None

    return container_node.createNode(node_name)

def setHdaParameters(work_item, hda_node):
    if hasStrData(work_item, 'hdaparms_strings'):
        str_parm_names = strDataArray(work_item, 'hdaparms_strings')
    else:
        str_parm_names = []

    if hasStrData(work_item, 'hdaparms_ints'):
        int_parm_names = strDataArray(work_item, 'hdaparms_ints')
    else:
        int_parm_names = []

    if hasStrData(work_item, 'hdaparms_floats'):
        float_parm_names = strDataArray(work_item, 'hdaparms_floats')
    else:
        float_parm_names = []

    if hasStrData(work_item, 'hdaparms_buttons'):
        button_parm_names = strDataArray(work_item, 'hdaparms_buttons')
    else:
        button_parm_names = []

    for int_parm in int_parm_names:
        parm_vals = intDataArray(work_item, int_parm)

        if len(parm_vals) > 1:
            node_parm = hda_node.parmTuple(int_parm)
            
            if len(node_parm) != len(parm_vals):
                printlog(
                    "WARNING: Parameter {} tuple size ({}) does not match the "
                    " tuple size of the provided parameter values: {}".format(
                    int_parm, len(node_parm), parm_vals))
                continue

            val = []
            for v in parm_vals:
                val.append(int(v))
        else:
            node_parm = hda_node.parm(int_parm)
            val = int(parm_vals[0])

        if node_parm:
            printlog("Setting parm {} to: {}".format(int_parm, parm_vals))
            node_parm.deleteAllKeyframes()
            node_parm.set(val)

    for float_parm in float_parm_names:
        parm_vals = floatDataArray(work_item, float_parm)

        if len(parm_vals) > 1:
            node_parm = hda_node.parmTuple(float_parm)

            if len(node_parm) != len(parm_vals):
                printlog(
                    "WARNING: Parameter {} tuple size ({}) does not match the "
                    "tuple size of the provided parameter values: {}".format(
                    float_parm, len(node_parm), parm_vals))
                continue

            val = parm_vals
        else:
            node_parm = hda_node.parm(float_parm)
            val = parm_vals[0]

        if node_parm:
            printlog("Setting parm {} to: {}".format(float_parm, parm_vals))
            node_parm.deleteAllKeyframes()
            node_parm.set(val)

    for str_parm in str_parm_names:
        parm_vals = strDataArray(work_item, str_parm)

        for i, parm_val in enumerate(parm_vals):
            parm_vals[i] = localizePath(parm_val)

        if len(parm_vals) > 1:
            node_parm = hda_node.parmTuple(str_parm)

            if len(node_parm) != len(parm_vals):
                printlog(
                    "WARNING: Parameter {} tuple size ({}) does not match the "
                    "tuple size of the provided parameter values: {}".format(
                    str_parm, len(node_parm), parm_vals))
                continue

            val = parm_vals
        else:
            node_parm = hda_node.parm(str_parm)
            val = parm_vals[0]

        if node_parm:
            printlog("Setting parm {} to: {}".format(str_parm, parm_vals))
            node_parm.deleteAllKeyframes()
            node_parm.set(val)

    for button_parm in button_parm_names:
        press = intDataArray(work_item, 'button_'+button_parm)[0]

        if press:
            node_parm = hda_node.parm(button_parm)

            if node_parm:
                printlog("Pressing {} parm button".format(button_parm))
                node_parm.pressButton()

def getCookErrors(node, get_nested_errors):
    err_msg = ""

    if get_nested_errors:
        cook_errors = composeNestedErrorMessages(node)
        err_msg = "\n".join(cook_errors)
    else:
        cook_errors = node.errors()
        if cook_errors:
            err_msg = "\n".join(cook_errors)

    return err_msg

def composeNestedErrorMessages(node):
    cook_errors = []
    node_errors = node.errors()
    if node_errors:
        err_str = "\n".join(node_errors)
        err_msg = "Error from node <{}>:\n{}".format(node.path(), err_str)
        cook_errors.append(err_msg)

    children = node.children()
    for c in children:
        cook_errors.extend(composeNestedErrorMessages(c))

    return cook_errors

class HdaService():
    def __init__(self):
        self.installed_hdas = {}

    @staticmethod
    def _job_success(outputs=None, dumpdebug=False, debug_hip_file=None):
        if dumpdebug:
            hou.hipFile.save(localizePath(debug_hip_file))
        return { 'status' : 'success', 'outputs' : outputs }

    @staticmethod
    def _job_failed(dumpdebug=False, debug_hip_file=None):
        if dumpdebug:
            hou.hipFile.save(localizePath(debug_hip_file))
        return { 'status' : 'failed' }

    @staticmethod
    def _writeSopOutputs(sop_node, work_item, temp_dir, dumpdebug):
        try:
            sop_node.cook()
        except hou.OperationFailed:
            err_msg = getCookErrors(sop_node,
                work_item.intAttribValue("reportnestederrors"))
            printlog("ERROR: Node failed to cook:\n{}".format(err_msg))

            return False, HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))

        outputfiles = intDataArray(work_item, 'outputfiles')[0]

        if outputfiles < 1:
            printlog("ERROR: Number of outputs must be at least 1 when "
                "`Write Output` is enabled.")

            return False, HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))

        outputpaths = strDataArray(work_item, 'outputpath')
        outputtags = strDataArray(work_item, 'outputtag')

        output_files = []

        for f in range(0, outputfiles):
            outputpath = localizePath(outputpaths[f])
            outputtag = outputtags[f]
            geometry = sop_node.geometry(f)

            if not geometry:
                printlog(
                    "ERROR: Output {} has no geometry to save".format(str(f)))

                return False, HdaService._job_failed(
                    dumpdebug, work_item.stringAttribValue('debughipfile'))

            makeDirSafe(os.path.dirname(outputpath))
            geometry.saveToFile(outputpath)

            output_files.append({ 'file' : delocalizePath(outputpath),
                                  'tag' : outputtag })

        return True, output_files

    def _configure_file_input(self, file_node, on_missing_input):
        filenode_missing_parm = file_node.parm('missingframe')

        # COP file nodes do not have this parm
        if not filenode_missing_parm:
            return

        if (on_missing_input == ON_MISSING_WARN or
            on_missing_input == ON_MISSING_IGNORE):
            filenode_missing_parm.set("empty")
        else:
            filenode_missing_parm.set("error")

    def _setup_file_inputs(self, work_item, hda):
        createfileinput = intDataArray(work_item, 'createfileinput')[0]
        missinginput = intDataArray(work_item, 'missinginput')[0]

        if createfileinput:
            numfileinputs = intDataArray(work_item, 'fileinputs')[0]
        else:
            numfileinputs = 0

        scene_obj = self.installed_hdas[hda]

        if scene_obj['category'] != 'Sop' and scene_obj['category'] != 'Cop2':
            return

        if len(self.installed_hdas[hda]['file_inputs']) == numfileinputs:
            for i in range(0, numfileinputs):
                filenode = self.installed_hdas[hda]['file_inputs'][i]
                self._configure_file_input(filenode, missinginput)
                self.installed_hdas[hda]['hda_node'].setInput(i, filenode)
        elif len(self.installed_hdas[hda]['file_inputs']) < numfileinputs:
            parent_net = self.installed_hdas[hda]['hda_node'].parent()
            for i in range(0, numfileinputs):
                try:
                    filenode = self.installed_hdas[hda]['file_inputs'][i]
                except IndexError:
                    filenode = parent_net.createNode('file')
                    self.installed_hdas[hda]['file_inputs'].append(filenode)

                self._configure_file_input(filenode, missinginput)
                self.installed_hdas[hda]['hda_node'].setInput(i, filenode)
        else:
            for i in range(0, len(self.installed_hdas[hda]['file_inputs'])):
                if i in range(0, numfileinputs):
                    filenode = self.installed_hdas[hda]['file_inputs'][i]
                    self._configure_file_input(filenode, missinginput)
                    self.installed_hdas[hda]['hda_node'].setInput(i, filenode)
                else:
                    self.installed_hdas[hda]['hda_node'].setInput(i, None)

        return

    def _setup_scene(self, work_item, temp_dir):
        hda = localizePath(strDataArray(work_item, 'hda')[0])
        dumpdebug = intDataArray(work_item, 'dumpdebug')[0]
        user_hda_path = hda
        printlog("HDA: %s" % hda)

        if work_item.hasFrame:
            printlog("Cooking frame %.1f" % work_item.frame)
            hou.setFrame(work_item.frame)

        # Now get the absolute path in case the user specified a relative path
        # (or perhaps just the name of an HDA which is on their OTL path)
        hda = getInputFilePath(hda, temp_dir)
        operatortype = strDataArray(work_item, 'operatortype')[0]
        printlog("Operator Type: %s" % operatortype)

        try:
            hda_def = getHdaDefinition(hda, operatortype)
        except hou.OperationFailed:
            printlog("ERROR: The HDA <{}> could not be found".format(
                user_hda_path))

            return HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))

        if not hda_def:
            printlog("ERROR: Operator type was not found in the asset "
                "definition")

            return HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))

        if not hda_def.isInstalled():
            installHda(hda)

        if hda in self.installed_hdas:
            scene_obj = self.installed_hdas[hda]
        else:
            scene_obj = { 'category' : None,
                          'hda_node' : None,
                          'file_inputs' : [] }

        category = getAssetDefinitionCategoryName(hda_def)
        scene_obj['category'] = category

        if category == 'Sop':
            if not scene_obj['hda_node']:
                sopnet = createNode('/obj', 'geo')
                hda_node = createNode('/obj/{}'.format(sopnet.name()),
                                      hda_def.nodeTypeName())
                scene_obj['hda_node'] = hda_node

            self.installed_hdas[hda] = scene_obj

            self._setup_file_inputs(work_item, hda)
        elif category == 'Object':
            if not scene_obj['hda_node']:
                hda_node = createNode('/obj', hda_def.nodeTypeName())
                scene_obj['hda_node'] = hda_node
            self.installed_hdas[hda] = scene_obj
        elif category == 'Cop2':
            if not scene_obj['hda_node']:
                copnet = createNode('/obj', 'cop2net')
                hda_node = createNode('/obj/{}'.format(copnet.name()),
                                      hda_def.nodeTypeName())
                scene_obj['hda_node'] = hda_node
            self.installed_hdas[hda] = scene_obj
            self._setup_file_inputs(work_item, hda)
        elif category == 'Lop':
            if not scene_obj['hda_node']:
                hda_node = createNode('/stage', hda_def.nodeTypeName())
                scene_obj['hda_node'] = hda_node
            self.installed_hdas[hda] = scene_obj

        return True

    def run_job(self, work_item, temp_dir):
        printlog("Running Houdini {} with PID {}".format(
            hou.applicationVersionString(), os.getpid()))

        # Scene setup
        result = self._setup_scene(work_item, temp_dir)

        if result is not True:
            return result

        hda = localizePath(strDataArray(work_item, 'hda')[0])
        hda = getInputFilePath(hda, temp_dir)

        # Cooking stage
        writeoutput = intDataArray(work_item, 'writeoutput')[0]
        dumpdebug = intDataArray(work_item, 'dumpdebug')[0]

        createfileinput = intDataArray(work_item, 'createfileinput')[0]
        missinginput = intDataArray(work_item, 'missinginput')[0]

        if createfileinput:
            numfileinputs = intDataArray(work_item, 'fileinputs')[0]
        else:
            numfileinputs = 0

        scene = self.installed_hdas[hda]

        if scene['category'] == 'Sop':
            if scene['file_inputs'] and numfileinputs > 0:
                inputpaths = strDataArray(work_item, 'inputpath')

                for i in range(0, numfileinputs):
                    filenode = scene['file_inputs'][i]
                    printlog("Setting input {} to {}".format(
                        i, localizePath(inputpaths[i])))
                    filenode_parm = filenode.parm('file')
                    filenode_parm.set(localizePath(inputpaths[i]))

                    if (not os.path.exists(localizePath(inputpaths[i]))
                        and missinginput == ON_MISSING_WARN):
                        work_item.cookWarning("Input file {} at <{}> "
                                "does not exist.".format(str(i+1), inputpaths[i]))

            setHdaParameters(work_item, scene['hda_node'])

            if writeoutput:
                success, result = HdaService._writeSopOutputs(
                                                scene['hda_node'],
                                                work_item,
                                                temp_dir,
                                                dumpdebug)

                if not success:
                    return result

                return HdaService._job_success(
                        outputs=result,
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))

            else:
                # Just cook it since no outputs were requested
                try:
                    scene['hda_node'].cook()
                    return HdaService._job_success(
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))
                except hou.OperationFailed:
                    err_msg = getCookErrors(scene['hda_node'],
                        work_item.intAttribValue('reportnestederrors'))
                    printlog("ERROR: Node failed to cook\n{}".format(err_msg))

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

        elif scene['category'] == 'Object':
            setHdaParameters(work_item, scene['hda_node'])

            if writeoutput:
                sopnet = None
                sopname = strDataArray(work_item, 'hdasopname')[0]
                children = scene['hda_node'].children()

                if not sopname:
                    printlog(
                        "WARNING: An object level operator is being used and no "
                        "SOP node was specified. The first valid displayed SOP "
                        "node will be used.")
                    for child in children:
                        try:
                            if child.isObjectDisplayed():
                                sopname = child.name()
                                sopnet = child.displayNode()
                                break
                        except:
                            pass
                    if not sopnet:
                        sopnet = scene['hda_node'].displayNode()
                else:
                    if sopname.startswith("./"):
                        sopnode = scene['hda_node'].node(sopname)
                        sopnet = sopnode.displayNode()
                        if not sopnet:
                            sopnet = sopnode
                    else:
                        for child in children:
                            if child.name() == sopname:
                                # Subnet
                                sopnet = child.displayNode()
                                # If not, then it's a regular SOP node
                                if not sopnet:
                                    sopnet = child
                                break

                if not sopnet:
                    printlog(
                        "ERROR: Specified SOP node was not found in the HDA")

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

                success, result = HdaService._writeSopOutputs(sopnet,
                                                              work_item,
                                                              temp_dir,
                                                              dumpdebug)

                if not success:
                    return result

                return HdaService._job_success(
                    outputs=result,
                    dumpdebug=dumpdebug,
                    debug_hip_file=work_item.stringAttribValue('debughipfile'))

            else:
                try:
                    scene['hda_node'].cook()
                    return HdaService._job_success(
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))
                except hou.OperationFailed:
                    err_msg = getCookErrors(scene['hda_node'],
                        work_item.intAttribValue("reportnestederrors"))
                    printlog("ERROR: Node failed to cook\n{}".format(err_msg))

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

        elif scene['category'] == 'Cop2':
            if scene['file_inputs'] and numfileinputs > 0:
                inputpaths = strDataArray(work_item, 'inputpath')

                for i, filenode in enumerate(scene['file_inputs']):
                    printlog("Setting input {} to {}".format(
                        i, localizePath(inputpaths[i])))
                    filenode_parm = filenode.parm('filename1')
                    filenode_parm.set(localizePath(inputpaths[i]))

            setHdaParameters(work_item, scene['hda_node'])

            if writeoutput:
                outputfiles = intDataArray(work_item, 'outputfiles')[0]

                if outputfiles < 1:
                    printlog("ERROR: Number ouf outputs must be at least 1 "
                        "when `Write Outputs` is enabled")

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

                outputpaths = strDataArray(work_item, 'outputpath')
                outputtags = strDataArray(work_item, 'outputtag')

                outputpath = localizePath(outputpaths[0])
                makeDirSafe(os.path.dirname(outputpath))

                try:
                    scene['hda_node'].cook()
                    scene['hda_node'].saveImage(outputpath)

                    output_files = [{ 'file' : delocalizePath(outputpaths[0]),
                                      'tag' : outputtags[0] }]

                    return HdaService._job_success(
                        outputs=output_files,
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))
                except hou.OperationFailed:
                    err_msg = getCookErrors(scene['hda_node'],
                        work_item.intAttribValue('reportnestederrors'))
                    printlog("ERROR: Node failed to cook\n{}".format(err_msg))

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

            else:
                try:
                    scene['hda_node'].cook()
                    return HdaService._job_success(
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))
                except hou.OperationFailed:
                    err_msg = getCookErrors(scene['hda_node'],
                        work_item.intAttribValue('reportnestederrors'))
                    printlog("ERROR: Node failed to cook\n{}".format(err_msg))

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

        elif scene['category'] == 'Lop':
            setHdaParameters(work_item, scene['hda_node'])

            if writeoutput:
                outputfiles = intDataArray(work_item, 'outputfiles')[0]

                if outputfiles < 1:
                    printlog("ERROR: Number of outputs must be at least 1 "
                        "when `Write Outputs` is enabled")
                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))

                outputpaths = strDataArray(work_item, 'outputpath')
                outputtags = strDataArray(work_item, 'outputtag')

                output_files = []

                for f in range(0, outputfiles):
                    outputpath = localizePath(outputpaths[f])
                    outputtag = outputtags[f]

                    stage = scene['hda_node'].stage(f)
                    flattened_stage = stage.Flatten()

                    makeDirSafe(os.path.dirname(outputpath))
                    flattened_stage.Export(outputpath)

                    output_files.append({ 'file' : delocalizePath(outputpath),
                                          'tag' : outputtag})

                return HdaService._job_success(
                    outputs=output_files,
                    dumpdebug=dumpdebug,
                    debug_hip_file=work_item.stringAttribValue('debughipfile'))
            else:
                try:
                    scene['hda_node'].cook()
                    return HdaService._job_success(
                        dumpdebug=dumpdebug,
                        debug_hip_file=work_item.stringAttribValue('debughipfile'))
                except hou.OperationFailed:
                    err_msg = getCookErrors(scene['hda_node'],
                        work_item.intAttribValue('reportnestederrors'))
                    printlog("ERROR: Node failed to cook\n{}".format(err_msg))

                    return HdaService._job_failed(
                        dumpdebug, work_item.stringAttribValue('debughipfile'))
        else:
            printlog("ERROR: HDA category is not supported")
            return HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))

    def receive_job(self, job_info):
        try:
            work_item = pdg.WorkItem.loadJSONString(job_info['work_item'], True)
        except Exception:
            printlog("ERROR: Could not deserialize work item JSON file. "
                "The work item file may contain invalid JSON.")
            return HdaService._job_failed()

        temp_dir = job_info['temp_dir']
        working_dir = job_info['working_dir']

        os.environ['PDG_DIR'] = working_dir

        try:
            log_buffer = RedirectBuffer(sys.stdout, True)
            with log_buffer:
                result = self.run_job(work_item, temp_dir)
                result['msg'] = log_buffer.buffer()
                return result

        except Exception:
            dumpdebug = intDataArray(work_item, 'dumpdebug')[0]
            error = "ERROR: {}".format(traceback.format_exc())

            printlog(error)
            result = HdaService._job_failed(
                dumpdebug, work_item.stringAttribValue('debughipfile'))
            result['msg'] = error
            return error

    def is_alive(self):
        return True

def run_job_mode():
    try:
        item_name = os.environ['PDG_ITEM_NAME']
        temp_dir = os.path.expandvars(os.environ['PDG_TEMP'])
    except KeyError as exception:
        printlog("ERROR: %s must be in the environment" % exception)
        hou.exit(-1)

    data_file = getWorkItemJsonPath(item_name)
    work_item = pdg.WorkItem.loadJSONFile(data_file, True)
    hda_service = HdaService()

    batchmode = intDataArray(work_item, 'batchmode')[0]

    if batchmode == BATCH_MODE_OFF:

        result = hda_service.run_job(work_item, temp_dir)

        if result['status'] == 'success':
            if result['outputs']:
                for output_file in result['outputs']:
                    reportResultData(
                            output_file['file'],
                            work_item.id,
                            subindex=work_item.batchIndex,
                            result_data_tag=output_file['tag'],
                            hash_code=0)
            hou.exit(0)
        else:
            hou.exit(-1)

    else:

        index = None
        cookwhen = intDataArray(work_item, 'cookwhen')[0]

        if cookwhen == ALL_FRAMES:
            for sub_item in work_item.batchItems:
                index = sub_item.batchIndex
                workItemStartCook(work_item.id, sub_item.batchIndex)

                result = hda_service.run_job(sub_item, temp_dir)

                if result['status'] == 'success':
                    if result['outputs']:
                        for output_file in result['outputs']:
                            reportResultData(
                                    output_file['file'],
                                    work_item.id,
                                    subindex=index,
                                    result_data_tag=output_file['tag'],
                                    hash_code=0)
                else:
                    hou.exit(-1)

                workItemSuccess(work_item.id, sub_item.batchIndex)
        elif cookwhen == FIRST_FRAME:
            for x in range(0, work_item.batchSize):
                waitUntilReady(work_item.id, x)
                json_item = getWorkItemJSON(work_item.id, x)
                sub_item = pdg.WorkItem.loadJSONString(json_item, True)
                index = sub_item.batchIndex
                workItemStartCook(work_item.id, sub_item.batchIndex)

                result = hda_service.run_job(sub_item, temp_dir)

                if result['status'] == 'success':
                    if result['outputs']:
                        for output_file in result['outputs']:
                            reportResultData(
                                    output_file['file'],
                                    work_item.id,
                                    subindex=index,
                                    result_data_tag=output_file['tag'],
                                    hash_code=0)
                else:
                    hou.exit(-1)

                workItemSuccess(work_item.id, x)

        hou.exit(0)

def run_service_mode(address, port, client_name):
    if not address or not port:
        printlog("ERROR: both address and port of the HDA MQ server must be "
            "supplied by command line args to this job script")
        hou.exit(-1)

    hda_service = HdaService()

    mqSetLogLevel(PDGNetLogLevel.PDGN_LOG_ALL)
    service_client = PDGNetMQRelay(hda_service)
    service_client.connectToMQServer(address, port, client_name, 50,
                        1000, 1000, 1000)

    serviceClientReady = PDGNetRPCMessage(address, port,
                            (client_name + '_init_listener'), False,
                            'serviceClientReady', 60000)
    serviceClientReady({'name' : client_name})

    import time
    while True:
        time.sleep(0.1)

        if not service_client.isConnected():
            service_client.stopAll()
            hou.exit(0)

def main():
    parser = argparse.ArgumentParser(description="HDA Processor Job Script")
    parser.add_argument('--service_mode', dest='service_mode', action='store_true')
    parser.add_argument('--no_service_mode', dest='service_mode', action='store_false')
    parser.set_defaults(service_mode=False)
    parser.add_argument('--address', dest='address')
    parser.add_argument('--port', dest='port')
    parser.add_argument('--logfile', dest='logfile')
    parser.add_argument('--client_name', dest='client_name')

    args = parser.parse_args()

    if args.logfile:
        logfile_path = os.path.expandvars(args.logfile)
        file_logging = True

        if not os.path.exists(logfile_path):
            try:
                os.makedirs(os.path.dirname(logfile_path))
            except OSError:
                if not os.path.isdir(os.path.dirname(logfile_path)):
                    file_logging = False

        if file_logging:
            sys.stdout = open(os.path.expandvars(logfile_path), 'w')
        else:
            sys.stdout = open(os.devnull, 'w')

    service_mode = args.service_mode

    if service_mode:
        address = args.address
        port = int(args.port)
        client_name = args.client_name
        run_service_mode(address, port, client_name)
    else:
        run_job_mode()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        printlog(traceback.format_exc())
        hou.exit(-1)
