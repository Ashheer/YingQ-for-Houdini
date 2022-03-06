
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
# NAME:	        rop.py ( Python )
#
# COMMENTS:     Utility methods for working with rop nodes
#

from __future__ import print_function, absolute_import

import argparse
import errno
import glob
from io import TextIOWrapper, BytesIO
import os
import re
import shlex
import socket
import sys
import time
import traceback

from pdgutils import PDGNetMQRelay

try:
    # when running on the farm, try to use the pdgcmd module
    # that was copied as a file-dependency.
    from pdgcmd import waitUntilReady, workItemSuccess, workItemStartCook
    from pdgcmd import reportResultData, getWorkItemJSON
    from pdgcmd import disable_rpc, localizePath, warning, printlog
    from pdgcmd import RedirectBuffer

    from pdgjson import WorkItem
    from pdgnetrpc import PDGNetRPCMessage
except ImportError:
    from pdgjob.pdgcmd import waitUntilReady, workItemSuccess, workItemStartCook
    from pdgjob.pdgcmd import reportResultData, getWorkItemJSON
    from pdgjob.pdgcmd import disable_rpc, localizePath, warning, printlog
    from pdgjob.pdgcmd import RedirectBuffer

    from pdgjob.pdgjson import WorkItem
    from pdgjob.pdgnetrpc import PDGNetRPCMessage

import hqueue.houdini as hq
import hou
import pdg

def resolveParm(node, parm_name):
    # Return a list of hou.Parm for the given name.  Will have a single entry
    # unless it's a multiparm instance like outputfile_#
    parms = []
    if '#' in parm_name:
        i = 1
        while True:
            parm_name_x = parm_name.replace('#', str(i))
            parm_x = node.parm(parm_name_x)
            if not parm_x:
                break
            parms.append(parm_x)
            i += 1
            
    else:
        parm = node.parm(parm_name)
        if parm:
            parms.append(parm)
    return parms

def resolveParms(node, custom_parms, frame, verbose):
    output_parms = []

    if custom_parms:
        if verbose:
            printlog("Checking for custom output parms '{}' on node '{}'".format(
                custom_parms, node.path()))

        for custom_parm in custom_parms:
            parms = resolveParm(node, custom_parm)
            if parms:
                output_parms += parms

    if not output_parms:
        if verbose:
            printlog("Checking for default output parms on node '{}'".format(
                custom_parms, node.path()))

        save_ifd = node.parm('soho_outputmode')
        if save_ifd:
            if frame:
                should_save = save_ifd.evalAtFrame(frame)
            else:
                should_save = save_ifd.eval()

            if should_save:
                ifd_path = node.parm('soho_diskfile')
                if ifd_path:
                    return [ifd_path]

        parm_names = ['vm_picture', 'sopoutput', 'dopoutput', 'lopoutput',
            'picture', 'copoutput', 'filename', 'usdfile', 'file', 'output',
            'outputfilepath', 'outputimage', 'outfile#']

        for parm_name in parm_names:
            output_parms = resolveParm(node, parm_name)
            if output_parms:
                break

    if not output_parms:
        if verbose:
            printlog("WARNING: No output parms found on node '{}'".format(
                node.path()))
        return []

    filtered_parms = []

    for parm in output_parms:
        if parm.name() == "vm_picture":
            # this is a mantra non-ifd render - use the python filter instead,
            # this gives us a more accurate method of reporting result artifacts
            soho_pipecmd = node.parm('soho_pipecmd')
            if soho_pipecmd:
                if frame:
                    mantra_cmd = soho_pipecmd.evalAtFrame(frame)
                else:
                    mantra_cmd = soho_pipecmd.eval()

                mantra_argv = shlex.split(mantra_cmd)
                if "-P" not in mantra_argv:
                    this_path = __file__.replace('\\', '/')
                    mantra_cmd = mantra_cmd + ' -P "{}"'.format(this_path)
                    soho_pipecmd.set(mantra_cmd)
                    continue

        filtered_parms.append(parm)

    return filtered_parms

def applyEnvironment(work_item):
    if not work_item:
        return

    environment = work_item.environment
    for k, v in list(environment.items()):
        hou.hscript('setenv {}={}'.format(k, v))
    hou.hscript('varchange')

def applyParm(parm, index, name, value, value_type, channel, verbose):
    if value_type == 0:
        expr = "@{}.{}".format(name, index)
        is_expr = True
    elif value_type == 1:
        value = value
        is_expr = False
    else:
        expr = str(value)
        is_expr = True

    if is_expr:
        try:
            if parm.expression() == expr:
                printlog('Parm "{}" at index "{}" was unchanged'
                    ' for the current subitem'.format(channel, index))
                return False
        except:
            pass

    parm.deleteAllKeyframes()

    if is_expr:
        if verbose:
            printlog('Setting parm "{}" at index "{}" to expr "{}"'.format(
                channel, index, expr))
        parm.setExpression(expr, hou.exprLanguage.Hscript)
    else:
        if verbose:
            printlog('Setting parm "{}" at index "{}" to value "{}"'.format(
                channel, index, value))
        parm.set(value)

    return True

def applyChannels(work_item, verbose=True):
    if not work_item:
        return

    # Check for wedge attributes
    attribs = work_item.stringAttribArray("wedgeattribs")
    if not attribs:
        return

    # For each attrib, try to find a channel array and value array
    for attrib_name in attribs:
        value_attrib = work_item.attrib(attrib_name)
        channel_attrib = work_item.attrib("{}channel".format(attrib_name))
        if not channel_attrib or not value_attrib:
            continue

        channel = channel_attrib.value()
        if value_attrib.type == pdg.attribType.Int:
            # Work around for a possibly HOM issue, which makes it not
            # possible to set a Long value to a parm
            value_array = [int(val) for val in value_attrib.values]
        else:
            value_array = value_attrib.values

        if verbose:
            printlog('Setting channels for wedge attrib "{}"'.format(
                attrib_name))

        value_type_attrib = work_item.attrib("{}valuetype".format(attrib_name))
        if value_type_attrib:
            value_type = value_type_attrib.value()
        else:
            value_type = 0

        if verbose:
            if value_type == 0:
                value_type_str = "Attribute Reference"
            elif value_type == 1:
                value_type_str = "Parameter Value"
            elif value_type == 2:
                value_type_str = "Parameter Expression"
            printlog('Setting value for "{}" with type "{}"'.format(
                attrib_name, value_type_str))

        # Apply values to channels
        parm = hou.parm(channel)
        if parm:
            applyParm(parm, 0, attrib_name, value_array[0],
                value_type, channel, verbose)
        else:
            parm_tuple = hou.parmTuple(channel)
            if parm_tuple:
                idx = 0
                for parm in parm_tuple:
                    applyParm(parm, idx, attrib_name, value_array[0],
                        value_type, channel, verbose)
                    idx += 1
            elif verbose:
                printlog('Parm "{}" not found'.format(channel))

def stepWorkItem(batch_index, is_comp):
    # Advance the selected work item
    pdg.EvaluationContext.setGlobalSubItemIndex(batch_index, is_comp)
    item = pdg.EvaluationContext.workItem()

    # Apply channels and env vars for the  current sub item
    applyChannels(item)
    applyEnvironment(item)

    # Update time dependent attributes. This is not needed for cop networks
    # because we force dirty all work item dependencies
    if not is_comp:
        if hasattr(item, "timeDependentAttribs"):
            pdg.EvaluationContext.setTimeDependentAttribs(
                item.timeDependentAttribs())
        else:
            printlog('Using old-style time dependent attributes for item -- '
                'the current Houdini version does not have new API methods. '
                'Update Houdini to fix this issue.')
            pdg.EvaluationContext.setTimeDependentAttribs(True)

    return item

def pressReloadParm(reload_parm_path):
    if reload_parm_path:
        reload_parm = hou.parm(reload_parm_path)
        if reload_parm:
            try:
                reload_parm.pressButton()
                printlog('Pressed reload button "{}"'.format(
                    reload_parm_path))
            except:
                printlog('WARNING: Unable to press reload button "{}"'.format(
                    reload_parm_path))
        else:
            printlog('WARNING: Invalid reload parm path "{}"'.format(
                reload_parm_path))

def collectOutput(node, custom_parms, frame, verbose):
    # Returns a list of outputs at the given frame for the node with the given
    # hou.Parm.  Note that the hou.Parm may be for a different ROP when cooking
    # up a ROP chain.
    outputs = []
    parms = resolveParms(node, custom_parms, frame, verbose)

    for parm in parms:
        if frame:
            outputs.append(parm.evalAtFrame(frame))
        else:
            outputs.append(parm.eval())

    return outputs

def collectOutputs(node, custom_parms, frame, verbose):
    # Add outputs for any input ROPs that have output file paths
    rop_stack = [node, ]
    visited_rops = []

    outputs = []
    while len(rop_stack) > 0:
        cur_rop = rop_stack.pop()
        output = collectOutput(cur_rop, custom_parms, frame, verbose)
        if output:
            outputs.extend(output)

        visited_rops.append(cur_rop)
        for input_node in cur_rop.inputs():
            if input_node is None:
                continue
            if input_node.type().category() == hou.ropNodeTypeCategory() \
                and input_node not in visited_rops:
                rop_stack.append(input_node)
    return outputs

def preFrame(item_name, workitem_id, server_addr, start, step, node_path,
             custom_parms, reload_parm_path, poll, single_task):
    node = hou.node(node_path)
    is_first = (hou.frame() == start)

    # If the work item is is cooking multiple frames, store the results in a
    # list so they can be reported at the end.
    if single_task:
        outputs = collectOutputs(node, custom_parms, None, is_first)
        if not hasattr(pdg, "__resultstash"):
            pdg.__resultstash = list()
        for output in outputs:
            pdg.__resultstash.append(output)
        return

    # Press the reload parm if one was set
    pressReloadParm(reload_parm_path)

    # Compute the batch index from the current frame
    batch_index = int(round((hou.frame() - start)/step))

    # If the batch starts when the first frame is ready, we need to poll for 
    # subsequent frame dependencies
    if poll:
        # Wait for the frame to be ready. Note this will throw an exception
        # when an upstream dependency for this frame fails, halting
        # execution.
        waitUntilReady(workitem_id, batch_index, server_addr)
        printlog('Requesting sub item {} for batch {}'.format(
            batch_index, item_name))
   
        # Request the work item when the batch frame is ready to run and 
        # load it into the graph
        json = getWorkItemJSON(workitem_id, batch_index, server_addr)
        item = pdg.WorkItem.loadJSONString(json, True)
        if item:
            printlog('Successfully loaded sub item {}'.format(item.name))
        else:
            printlog('Failed to load sub item at index {}'.format(
                batch_index))

    printlog('Setting batch sub index to {}'.format(batch_index))

    # Advanced the active work item
    is_comp = (node.type().name() == "comp")
    item = stepWorkItem(batch_index, is_comp)

    # Stash the result for this frame. We do this now since the result may
    # not be immediately used in the postframe if the rop uses the postwrite/
    # background writing feature. During the postwrite our active work item will
    # change since the next frame will be simulating, so we need to eval the
    # output path now and save it before that happens
    outputs = collectOutputs(node, custom_parms, None, is_first)
    if not hasattr(pdg, "__resultstash"):
        pdg.__resultstash = dict()
    pdg.__resultstash[batch_index] = outputs

    for output in outputs:
        item.addExpectedResultData(output, "")

    # Notify PDG that the batch item is now cooking
    workItemStartCook(workitem_id, batch_index, server_addr, raise_exc=False)

def postFrame(workitem_id, server_addr, start, step, node_path, file_tag):
    # Compute the batch index from the current frame
    batch_index = int(round((hou.frame() - start)/step))
    printlog('postFrame {}'.format(batch_index))
    # If there's a stashed result for our index, report it to PDG. Always 
    # report sub item success in both cases.
    if hasattr(pdg, "__resultstash") and batch_index in pdg.__resultstash:
        output_files = pdg.__resultstash[batch_index]
        
        for output_file in output_files:
            reportResultData(output_file, workitem_id=workitem_id,
                subindex=batch_index, result_data_tag=file_tag,
                server_addr=server_addr, and_success=False, raise_exc=False)
        workItemSuccess(workitem_id, subindex=batch_index, raise_exc=False)
    else:
        workItemSuccess(workitem_id, subindex=batch_index, raise_exc=False)

    # The Composite ROP will determine its output file before the pre-frame is
    # called, so we need to advance the work item now
    node = hou.node(node_path)

    is_comp = (node.type().name() == "comp")
    if is_comp:
        stepWorkItem(batch_index + 1, True)

def stashCallbackParm(cb_node, callback_parm, callback_name):
    parm_label = '__pdg_{}'.format(callback_name)
    parm_template = hou.StringParmTemplate(parm_label, parm_label, 1)

    def stashCallbackInternal():
        stash_parm = cb_node.addSpareParmTuple(parm_template)
        stash_parm[0].setFromParm(callback_parm)
        callback_parm.deleteAllKeyframes()
        return stash_parm.node().path() + '/' + stash_parm.name()

    try:
        return stashCallbackInternal()
    except:
        parent = cb_node.parent()
        if parent:
            printlog("Unlocking " + parent.path())
            parent.allowEditingOfContents(True)
            return stashCallbackInternal()
        else:
            raise

def callStashedParm(stash):
    if not stash:
        return ""
    stashed_value = "hou.parmTuple('{}').evalAsStrings()[0]".format(stash[0])

    if stash[1] == 'hscript':
        return "hou.hscript({})".format(stashed_value)
    else:
        return "exec({})".format(stashed_value)

def needsStashedParm(node, parmname):
    parm = node.parm(parmname)
    tparm = node.parm('t'+parmname)

    if not parm or parm.isAtDefault():
        return None
    if tparm and not tparm.eval():
        return None

    return parm

def preFrameScript(stash, args):
    return """
try:
    import rop
except ImportError:
    from pdgjob import rop

{stashed}

n = hou.node({args[node_path]!r})
rop.preFrame(
    {args[item_name]!r},
    {args[work_item].id},
    {args[server]!r},
    {args[real_start]},
    {args[step]},
    {args[node_path]!r},
    {args[outputparm]},
    {args[reloadparm]!r},
    {args[batchpoll]},
    {args[singletask]})
""".format(args=args, stashed=callStashedParm(stash))

def postFrameScript(stash, args):
    return """
try:
    import rop
except ImportError:
    from pdgjob import rop

{stashed}

rop.postFrame(
    {args[work_item].id},
    {args[server]!r},
    {args[real_start]},
    {args[step]},
    {args[node_path]!r},
    {args[filetag]!r})
""".format(args=args, stashed=callStashedParm(stash))

def setCallbacks(cb_node, callbacks, warn):
    def setCallbacksInternal():
        for callback in callbacks:
            script = callbacks[callback]
            printlog("Setting callback {} on {}".format(callback, cb_node.path()))

            parm = cb_node.parm(callback)
            if parm:
                hq.setParmValue(parm, script)
            elif warn:
                printlog("WARNING: when batching is enabled, the specific ROP "
                    "({}) should have a {} callback.".format(
                        cb_node.path(), callback))

            parm = cb_node.parm('l'+callback)
            if parm:
                hq.setParmValue(parm, 'python')

            parm = cb_node.parm('t'+callback)
            if parm:
                hq.setParmValue(parm, 1)

    try:
        setCallbacksInternal()
    except hou.PermissionError:
        # Failed to modify node or parameter because of a permission error.
        # Possible causes include locked assets, takes, product permissions
        # or user specified permissions
        # Try recursing up to manager and unlocking any hdas
        parent = cb_node.parent()
        if parent:
            printlog("Unlocking " + parent.path())
            parent.allowEditingOfContents(True)
            setCallbacksInternal()
        else:
            raise

# Initializes a control dop node with tracker information, and sets the slice
# variables
def initDistSim(control_path, tracker_ip, tracker_port, slice_num, slice_type,
                slice_divs):

    printlog("Sim control DOP: {}".format(control_path))
    printlog("Sim tracker: {}:{}".format(tracker_ip, tracker_port))
    printlog("Sim slice type: {} number: {} divisions: {}".format(
        slice_type, slice_num, slice_divs))

    control = hq.getNode(control_path)
    control.parm('address').deleteAllKeyframes()
    control.parm('address').set(tracker_ip)
    control.parm('port').set(int(tracker_port))

    if slice_type == 0:
        pass    # nothing for particle slices as of yet
    elif slice_type == 1:
        # volume slices
        slice_div_parm = control.parmTuple("slicediv")
        vis_slice_div_parm = control.parmTuple("visslicediv")
        if slice_div_parm is not None:
            slice_div_parm.set(slice_divs)
        if vis_slice_div_parm is not None:
            vis_slice_div_parm.set(slice_divs)

    hou.hscript('setenv SLICE=' + str(slice_num))
    hou.hscript('varchange')

# Renders a ROP node with the specified arguments
def renderROP(node, button_name, *args, **kwargs):
    try:
        if button_name != "execute" and node.parm(button_name):
            printlog("Pressing button '{}' on node".format(button_name))
            node.parm(button_name).pressButton()
        elif hasattr(node, 'render'):
            printlog("Cooking node using 'hou.Rop.render'")
            node.render(*args, **kwargs)
        elif node.parm("execute"):
            printlog("Pressing button 'execute' on node")
            node.parm("execute").pressButton()
        else:
            printlog("ERROR: Unable to cook node '{}'. No render method or "
                "execute button was found".format(node.path()))
            return False

        if len(node.errors()):
            printlog("ERROR: {}".format(str(node.errors())))
            return False

    except hou.OperationFailed as e:
        printlog("ERROR: {}".format(str(e)))
        return False

    return True

def filterOutputAssets(assets):
    """
    mantra filter callback function
    """
    # asset file-type -> pdg data tag

    resulttag = { 0: 'image',
                  1: 'image/texture',
                  2: 'geo',
                  3: 'image/shadowmap',
                  4: 'image/photonmap',
                  5: 'image/envmap' }
    for asset in assets:
        for file_ in asset[1]:
            filename = file_[0]
            tag = ""
            try:
                tag = 'file/' + resulttag[file_[1]]
            except:
                pass
            reportResultData(filename, result_data_tag=tag, raise_exc=False)

class StdOutReporter(TextIOWrapper):
    def __init__(self):
        TextIOWrapper.__init__(self, BytesIO(), errors='replace')
        self.term = sys.stdout
        sys.stdout = self

    def reportAndClose(self):
        self.seek(0)
        while True:
            line = self.readline()
            if not line:
                break
            match = re.search(r'OUTPUT_FILE:(.*);(.*)', line)
            if match:
                output_file = match.group(1).strip()
                output_tag = match.group(2).strip()
                reportResultData(output_file, result_data_tag=output_tag,
                    to_stdout=False, raise_exc=False)
        self.close()
        sys.stdout = self.term
        self.term = None

    def write(self, s):
        self.term.write(s)
        self.term.flush()
        try:
            return super(StdOutReporter, self).write(s)
        except TypeError:
            # redirect encoded byte strings directly to buffer
            return super(StdOutReporter, self).buffer.write(s)

class RopCooker(object):
    # Prints debug information about the node
    def _printNodeInfo(self, node):
        node_type = node.type()
        if node.isSubNetwork() and node.type().childTypeCategory().name() == 'Driver':
            printlog("ROP subnet path '{}'".format(node.path()))
        else:
            printlog("ROP node path '{}'".format(node.path()))

        if node_type:
            printlog("ROP type name: '{}'".format(node_type.name()))
            printlog("ROP source path: '{}'".format(node_type.sourcePath()))
            hda_defn = node_type.definition()
            if hda_defn:
                printlog("ROP library path: '{}'".format(hda_defn.libraryFilePath()))

    # Cooks a node with a given start, end and increment
    def _cookNode(self, node, cb_node, args):
        start = args['real_start']
        end = args['real_end']
        inc = args['step']

        frame_range = (start, end, inc)

        if cb_node:
            try:
                # Force the ROP to use the "frame range" setting, in case it was
                # saved with the "cook the current frame" setting
                valid_range = cb_node.parm("trange")
                if valid_range is not None:
                    valid_range.set("normal")

                # Always show alf progress
                alf_progress = cb_node.parm("alfprogress")
                if alf_progress is not None:
                    alf_progress.set(1)
                else:
                    alf_progress = cb_node.parm("vm_alfprogress")
                    if alf_progress is not None:
                        alf_progress.set(1)

                # If the node we're going to cook is not a ROP-like node or
                # has a custom execute button, we need to try to override the
                # frame range parm. The node will just be cooked by pressing
                # button and doesn't accept a frame range arg.
                if not hasattr(node, 'render') or args['executeparm'] != "execute":
                    frame = cb_node.parmTuple("f")
                    frame.deleteAllKeyframes()
                    if frame is not None:
                        frame.set(frame_range)
            except:
                pass

        if args['cookorder'] == 0:
            cook_order = hou.renderMethod.RopByRop
        else:
            cook_order = hou.renderMethod.FrameByFrame

        profile = None
        perf_file = None
        if args['perfmon']:
            if args['perfmonfile']:
                profile = hou.perfMon.startProfile("ROP Fetch Cook")
                perf_file = args['perfmonfile']
            else:
                hou.hscript("perfmon -o stdout -t ms")

        if args['norange']:
            if not renderROP(node, args['executeparm'], method=cook_order):
                return False
        else:
            if not renderROP(node, args['executeparm'], frame_range, method=cook_order):
                return False

        if profile and perf_file:
            profile.stop()

            local_path = localizePath(perf_file)
            printlog("Saving performance file to %s" % local_path)

            # Ensure file path exists
            if not os.path.exists(os.path.dirname(local_path)):
                try:
                    os.makedirs(os.path.dirname(local_path))
                except OSError as exc:
                    if exc.errno != errno.EEXIST:
                        raise

            if local_path.endswith('csv'):
                profile.exportAsCSV(local_path)
            else:
                profile.save(local_path)

            if args['reportdebugoutputs']:
                reportResultData(
                    perf_file,
                    result_data_tag='file/text/debug',
                    raise_exc=False)

        return True

    # Cooks a single frame
    def cookSingleFrame(self, args):
        node = hq.getNode(args['node_path'])
        self._printNodeInfo(node)

        if node.isSubNetwork() and node.type().childTypeCategory().name() == 'Driver':
            is_ropnet = True
        else:
            is_ropnet = False

        cb_node = node
        if args['top_path']:
            cb_node = hq.getNode(args['top_path'])

        # Look for a parm named 'pdg_logoutput', if it exists it means that we
        # can toggle it on to get stdout logging messages to tell us what files
        # have be created.
        output_logger = None
        pdglogoutput = node.parm('pdg_logoutput')
        if pdglogoutput:
            output_logger = StdOutReporter()
            pdglogoutput.set(1)

        fg_parm = node.parm("soho_foreground")
        if fg_parm is not None:
            fg_parm.set(1)

        vm_tile_index = cb_node.parm("vm_tile_index")
        if vm_tile_index is not None and args['tileindex'] > -1:
            # this is a mantra tiled render
            vm_tile_index.set(int(args['tileindex']))

        parms = []
        if not pdglogoutput:
            parms = resolveParms(
                node, args['outputparm'], args['real_start'], True)

        # For single task work items, we need to set a pre frame script
        # that stashs per frame output files
        if args['singletask'] and parms:
            # Stash the existing preframe script
            preframe_parm = needsStashedParm(cb_node, 'preframe')
            
            if preframe_parm:
                stash_parm = stashCallbackParm(
                    cb_node, preframe_parm, 'preframe')
                pre_stash = (stash_parm,
                    cb_node.parm('lpreframe').eval())
                printlog("Saving existing user-defined 'preframe' script")
            else:
                pre_stash = None

            # Generate and set the batch pre/post scripts
            callbacks = {
                'preframe': preFrameScript(pre_stash, args)
            }

            setCallbacks(cb_node, callbacks, False)
        else:
            pressReloadParm(args['reloadparm'])

        if not self._cookNode(node, cb_node, args):
            return None

        results = []
        if pdglogoutput:
            output_logger.reportAndClose()
        elif parms:
            if args['singletask']:
                if hasattr(pdg, "__resultstash"):
                    result_stash = getattr(pdg, "__resultstash")
                    result_set = set()
                    for output_file in result_stash:
                        if output_file in result_set:
                            continue
                        result_set.update((output_file,))
                        result = (output_file, args['filetag'])
                        reportResultData(
                            result[0],
                            result_data_tag=result[1],
                            raise_exc=False)
                        results.append(result)
            else:
                output_files = collectOutputs(
                    node, args['outputparm'], args['real_start'], True)
                for output_file in output_files:
                    result = (output_file, args['filetag'])
                    reportResultData(
                        result[0],
                        result_data_tag=args['filetag'],
                        raise_exc=False)
                    results.append(result)
        elif is_ropnet:
            children = node.allSubChildren(True, False)
            printlog("Checking subnet children for output parms")
            for child in children:
                output_parms = resolveParms(
                    child, args['outputparm'], args['real_start'], True)

                if output_parms:
                    for p in output_parms:
                        result = (p.evalAtFrame(args['real_start']),
                            args['filetag'])
                        results.append(result)
                        reportResultData(
                            result[0],
                            result_data_tag=result[1],
                            raise_exc=False)

        return results

    # Cook a batch of frames, with a batch pool call made before the frame is
    # cooked and a batch notification emitted after each frame completes
    def cookBatchFrames(self, args):
        node = hq.getNode(args['node_path'])
        self._printNodeInfo(node)

        if node.isSubNetwork() and node.type().childTypeCategory().name() == 'Driver':
            is_ropnet = True
        else:
            is_ropnet = False

        cb_node = node
        if args['top_path']:
            cb_node = hq.getNode(args['top_path'])

        output_parms  = resolveParms(
            node, args['outputparm'], args['real_start'], True)

        if not output_parms and is_ropnet:
            printlog("ERROR: when batching is enabled, the specific ROP must "
                "have an output parameter and the preframe and postframe "
                "callbacks")
            hou.exit(1)

        # Check if the node uses postframe or postwrite
        if cb_node.parm('postwrite'):
            post_frame = 'postwrite'
        else:
            post_frame = 'postframe'

        # Stash the existing preframe script
        preframe_parm = needsStashedParm(cb_node, 'preframe')
        if preframe_parm:
            stash_parm = stashCallbackParm(
                cb_node, preframe_parm, 'preframe')
            pre_stash = (stash_parm,
                cb_node.parm('lpreframe').eval())
            printlog("Saving existing user-defined 'preframe' script")
        else:
            pre_stash = None

        # Stash the existing postframe script
        postframe_parm = needsStashedParm(cb_node, post_frame)
        if postframe_parm:
            stash_parm = stashCallbackParm(
                cb_node, postframe_parm, post_frame)
            post_stash = (stash_parm,
                cb_node.parm('l'+post_frame).eval())
            printlog("Saving existing user-defined '{}' script".format(
                post_frame))
        else:
            post_stash = None

        # Generate and set the batch pre/post scripts
        callbacks = {
            'preframe': preFrameScript(pre_stash, args),
            post_frame: postFrameScript(post_stash, args)
        }
        setCallbacks(cb_node, callbacks, True)

        start_index = args['batchstart']
        if start_index > 0:
            printlog("Report cached results for sub items 0 to {}".format(
                start_index-1))
            for i in range(0, start_index):
                frame = args['real_start'] + i*args['step']
                if args['batchpoll']:
                    waitUntilReady(args['work_item'].id, i, args['server'])

                last_out_ix = len(output_parms) - 1
                for out_ix, output_parm in enumerate(output_parms):
                    output_file = output_parm.evalAtFrame(frame)

                    reportResultData(output_file, workitem_id=args['work_item'].id,
                        subindex=i, result_data_tag=args['filetag'],
                        server_addr=args['server'],
                        and_success=out_ix == last_out_ix,
                        raise_exc=False)

            start_frame = args['real_start'] + start_index*args['step']
            printlog("Partial cook of batch from index={} frame={}".format(
                start_index, start_frame))
            args['real_start'] = start_frame

        return self._cookNode(node, cb_node, args)

class IndirectRopCooker(RopCooker):
    def __init__(self, path):
        self.path = path

    # Custom cook that calls the render method in the file cache node's internal
    # rop geometry instance
    def _cookNode(self, node, cb_node, args):
        start = args['real_start']
        end = args['real_end']
        inc = args['step']

        frame_range = (start, end, inc)

        if cb_node:
            try:
                # Force the ROP to use the "frame range" setting, in case it was
                # saved with the "cook the current frame" setting
                valid_range = cb_node.parm("trange")
                if valid_range is not None:
                    valid_range.set("normal")

                # Always show alf progress
                alf_progress = cb_node.parm("alfprogress")
                if alf_progress is not None:
                    alf_progress.set(1)
                else:
                    alf_progress = cb_node.parm("vm_alfprogress")
                    if alf_progress is not None:
                        alf_progress.set(1)

                # If the node we're going to cook is not a ROP-like node or
                # has a custom execute button, we need to try to override the
                # frame range parm. The node will just be cooked by pressing
                # button and doesn't accept a frame range arg.
                if not hasattr(node, 'render') or args['executeparm'] != "execute":
                    frame = cb_node.parmTuple("f")
                    frame.deleteAllKeyframes()
                    if frame is not None:
                        frame.set(frame_range)
            except:
                pass

        if args['cookorder'] == 0:
            cook_order = hou.renderMethod.RopByRop
        else:
            cook_order = hou.renderMethod.FrameByFrame

        inner_rop = node.node(self.path)

        if args['perfmon']:
            hou.hscript("perfmon -o stdout -t ms")

        cook_rop = inner_rop or node

        if inner_rop:
            printlog("Cooking internal ROP '{}' from subnet '{}'".format(
                inner_rop.path(), node.path()))

        if args['norange']:
            if not renderROP(cook_rop, args['executeparm'], method=cook_order):
                return False
        else:
            if not renderROP(cook_rop, args['executeparm'], frame_range, method=cook_order):
                return False

        if inner_rop:
            try:
                node.parm('reload').pressButton()
            except:
                pass

        return True

class BakeTextureCooker(RopCooker):
    def cookSingleFrame(self, args):
        node = hq.getNode(args['node_path'])
        cb_node = node
        if args['top_path']:
            cb_node = hq.getNode(args['top_path'])

        pre_cook_time = time.time()
        if not self._cookNode(node, cb_node, args):
            return None

        i = 1
        while True:
            outputparm = node.parm('vm_uvoutputpicture' + str(i))
            if outputparm:
                exp = outputparm.evalAtFrame(args['real_start'])
                if exp:
                    outdir, basename = os.path.split(exp)
                    # the naming rules are crazy - just search for a prefix
                    firstvar = basename.find('%')
                    basename = basename[:firstvar].strip("_.")
                    globpattern = outdir + "/" + basename + "*"
                    files = glob.glob(globpattern)
                    for file_ in files:
                        if os.stat(file_).st_mtime > pre_cook_time:
                            reportResultData(file_, result_data_tag=args['filetag'],
                                server_addr=args['server'], raise_exc=False)
            else:
                break
            i += 1
        return []

    def cookBatchFrames(self, args):
        raise RuntimeError("baketexture node not supported in batch mode")

class GamesBakerCooker(RopCooker):
    def cookSingleFrame(self, args):
        node = hq.getNode(args['node_path'])
        cb_node = node
        if args['top_path']:
            cb_node = hq.getNode(args['top_path'])

        vm_tile_index = cb_node.parm("vm_tile_index")
        if vm_tile_index is not None:
            vm_tile_index.set(args['tileindex'])

        pre_cook_time = time.time()

        # Since games baker is not a real rop, hq.render ignores the
        # frame range.  Since the frame range is not set to the desired range
        # in most cases we need to manually set the range parm here.
        frame_range = cb_node.parmTuple("f")
        if frame_range:
            frame_range.deleteAllKeyframes()
            frame_range.set((args['real_start'], args['real_end'], args['step']))

        if not self._cookNode(node, cb_node, args):
            return None

        # sop and rop bakers have this parm
        if node.parm('base_path'):
            exp = node.parm('base_path').evalAtFrame(args['real_start'])
            if exp:
                outdir, basename = os.path.split(exp)
                globpattern = outdir + "/" + re.sub(r'\$\(\w+\)', '*', basename)
                for f in glob.glob(globpattern):
                    if os.stat(f).st_mtime > pre_cook_time:
                        reportResultData(f, result_data_tag=args['filetag'],
                            server_addr=args['server'], raise_exc=False)

        # rop_games_baker won't have this parm
        if node.parm('export_fbx'):
            enable_fbx = node.parm('export_fbx').evalAtFrame(args['real_start'])
            if enable_fbx:
                fbx_path = node.parm('fbx_path').evalAtFrame(args['real_start'])
                if fbx_path:
                    f = fbx_path
                    if os.path.exists(f) and os.stat(f).st_mtime > pre_cook_time:
                        reportResultData(f, result_data_tag=args['filetag'],
                            server_addr=args['server'], raise_exc=False)
        return []

    def cookBatchFrames(self, args):
        raise RuntimeError("Gamesbaker nodes not supported in batch mode")

class RopService():

    def __init__(self):
        self.loaded_hip = None
        self.hipfile_modtime = None

    def is_alive(self):
        return True

    def receive_job(self, job_info):
        try:
            work_item = pdg.WorkItem.loadJSONString(
                job_info['work_item'], True)
        except Exception:
            return {"status" : "failed",
                    "msg" : "ERROR: Could not deserialize work item JSON file. "
                            "The work item file may contain invalid JSON."}

        pdg.EvaluationContext.setGlobalWorkItem(work_item, True)
        working_dir = job_info['working_dir']
        temp_dir = job_info['temp_dir']

        os.environ['PDG_DIR'] = working_dir
        os.environ['PDG_ITEM_NAME'] = work_item.name
        os.environ['PDG_ITEM_ID'] = str(work_item.id)

        log_buffer = RedirectBuffer(sys.stdout, True)
        with log_buffer:
            settings = _parseWorkItemFile(work_item)
            result = self.run_service_job(settings)
            result["msg"] = log_buffer.buffer()
            return result

        return {"status" : "failed"}

    def run_service_job(self, settings):
        printlog("PDG: Cooking work item in service mode")

        # cache the ip of the result server on the args to speed up rpc calls
        if not settings['server']:
            if not disable_rpc:
                hostname, port = os.environ['PDG_RESULT_SERVER'].split(':')
                settings['server'] = socket.gethostbyname(hostname) + ':' + port
            else:
                settings['server'] = ''

        saved_environ = os.environ.copy()

        # Load the file and apply and distributed sim initialization if needed
        hip_local = localizePath(settings['hip'])

        if not hq.fileExists(hip_local):
            printlog("PDG: Cannot find file %s" % hip_local)

        # convert to forward slash again to handle any windows-style path components
        # that have been expanded
        hip_local = hip_local.replace('\\', '/')
        mod_time = os.path.getmtime(hip_local)

        if (not self.loaded_hip
            or hip_local != self.loaded_hip
            or self.hipfile_modtime != mod_time):
            printlog("PDG: Loading .hip file %s." % hip_local)

            try:
                hou.hipFile.load(hip_local, ignore_load_warnings=True)
            except hou.OperationFailed as e:
                if settings['ignoreloaderrors']:
                    printlog(
                        "WARNING: Error(s) when loading .hip file:\n{}".format(str(e)))
                else:
                    raise e
            self.loaded_hip = hip_local
            self.hipfile_modtime = mod_time
        else:
            printlog("PDG: .hip file %s is already loaded" % hip_local)

        # re-apply saved env vars which may have been overwritten by those saved in
        # the hip file.
        dont_set = ('HIP', 'HIPFILE', 'HIPNAME', 'JOB', 'PI', 'E', 'POSE', 'status',
            'EYE', 'ACTIVETAKE')
        hscript_set = hou.hscript('set -s')
        for ln in hscript_set[0].split('\n'):
            if ln.startswith('set '):
                split_val = ln[7:].split(' = ')
                if not split_val:
                    continue
                key = split_val[0]
                if (key in saved_environ) and (key not in dont_set):
                    setcmd = "setenv {} = '{}'".format(key, saved_environ[key])
                    hou.hscript(setcmd)

        if settings['sethip']:
            # if $ORIGINAL_HIP exists, we use that instead of the supplied argument 
            # because current working dir might be different from the original hip dir
            original_hip = os.environ.get('ORIGINAL_HIP', settings['sethip'])
            original_hip = localizePath(original_hip)
            printlog('Resetting HIP to ' + original_hip)
            hou.hscript('set HIP={}'.format(original_hip))
            hou.hscript('set HIPFILE={}/{}'.format(original_hip, hip_local))

        hou.hscript('varchange')

        applyChannels(settings['work_item'])

        # Check for special-case nodes
        node = hq.getNode(settings['node_path'])
        node_type = node.type().name()

        # Set GPU overrides for Redshift
        if node_type == 'Redshift_ROP' and settings['gpus']:
            gpus = settings['gpus'].split(",")
            gpuSettingString = ""
            for i in range(8):
                if str(i) in gpus:
                    gpuSettingString += "1"
                else:
                    gpuSettingString += "0"
            hou.hscript("Redshift_setGPU -s " + gpuSettingString)

        if node_type.startswith('baketexture::'):
            cooker = BakeTextureCooker()
        elif node_type.find('sop_simple_baker') >= 0 or node_type.find('rop_games_baker') >= 0:
            cooker = GamesBakerCooker()
        else:
            op_map = {'filecache' : 'render',
                      'vellumio' : 'filecache1/render',
                      'dopio' : 'render',
                      'karma' : 'rop_usdrender'}

            found = False
            for op_name, rop_path in list(op_map.items()):
                if node_type.find(op_name) >= 0 and node.node(rop_path):
                    cooker = IndirectRopCooker(rop_path)
                    found = True
                    break

            if not found:
                cooker = RopCooker()

        try:
            # Cook based on the settings supplied by the user
            if settings['batch'] or settings['dist']:
                printlog("ERROR: ROP Fetch service does not support batching "
                    "distributed simulations")
                return {"status" : "failed"}
            else:
                results = cooker.cookSingleFrame(settings)
                if results is None:
                    return {"status" : "failed"}
                elif len(results):
                    output_files = []
                    for result in results:
                        output_files.append({"file" : result[0], "tag" : result[1]})
                    return {"status" : "success", "outputs" : output_files}
        except:
            printlog("ERROR: {}".format(traceback.format_exc()))
            return {"status" : "failed"}

        return {"status" : "success"}

def runServiceMode(args):
    address = args.address
    port = int(args.port)
    client_name = args.client_name

    printlog("Starting up service...")

    service = RopService()

    service_client = PDGNetMQRelay(service)
    service_client.connectToMQServer(address, port, client_name, 50,
                    5000, 1000, 5000)

    serviceClientReady = PDGNetRPCMessage(address, port,
                            (client_name + '_init_listener'), False,
                            'serviceClientReady', 60000)
    serviceClientReady({'name' : client_name})

    printlog("Service client ready.")

    import time
    while True:
        time.sleep(0.1)

        if not service_client.isConnected():
            service_client.stopAll()
            hou.exit(0)

def _parseArgs(args):
    settings = {}

    settings['server'] = args.server

    settings['item_name'] = args.item_name
    if not args.item_name:
        settings['item_name'] = os.environ['PDG_ITEM_NAME']

    work_item = WorkItem.fromJobEnvironment(as_native=True)
    applyChannels(work_item)

    settings['work_item'] = work_item

    settings['hip'] = args.hip
    settings['sethip'] = args.sethip
    settings['node_path'] = args.node_path
    settings['top_path'] = args.top_path
    settings['tileindex'] = args.tileix
    settings['start'] = args.start
    settings['end'] = args.end
    settings['real_start'] = args.start
    settings['real_end'] = args.end
    settings['step'] = args.step

    if args.outputparm:
        settings['outputparm'] = [args.outputparm]
        settings['usecustomoutputparm'] = 1
    else:
        settings['outputparm'] = []
        settings['usecustomoutputparm'] = 0

    settings['executeparm'] = args.executeparm

    settings['ignoreloaderrors'] = 0

    settings['filetag'] = args.tag
    settings['norange'] = args.norange
    settings['cookorder'] = 1
    settings['singletask'] = args.singletask

    settings['batch'] = args.batch
    settings['batchstart'] = 0

    if settings['batch']:
        settings['batchpoll'] = args.batchpoll
    else:
        settings['batchpoll'] = False

    settings['dist'] = args.dist

    if settings['dist']:
        settings['slicex'] = args.slicex
        settings['slicey'] = args.slicey
        settings['slicez'] = args.slicez
        settings['control'] = args.control
        settings['trackip'] = args.trackip
        settings['trackport'] = args.trackport
        settings['slice'] = args.slice
        settings['slicetype'] = args.slicetype

    settings['gpus'] = args.gpus
    settings['perfmon'] = False
    settings['perfmonfile'] = ""
    settings['debugfile'] = ""
    settings['reportdebugoutputs'] = ""

    return settings

def _parseWorkItemFile(work_item):
    settings = {}

    settings['server'] = None
    settings['item_name'] = work_item.name

    settings['work_item'] = work_item

    settings['hip'] = work_item.fileAttribValue('hip').path
    settings['sethip'] = work_item.stringAttribValue('sethip')
    settings['node_path'] = work_item.stringAttribValue('rop')
    settings['top_path'] = work_item.stringAttribValue('top')
    settings['tileindex'] = work_item.intAttribValue('tileindex')
    settings['start'] = work_item.floatAttribValue('range', 0)
    settings['end'] = work_item.floatAttribValue('range', 1)
    settings['step'] = work_item.floatAttribValue('range', 2)

    if work_item.hasAttrib("real_start"):
        settings['real_start'] = work_item.floatAttribValue('real_start', 0)
    else:
        settings['real_start'] = settings['start']

    if work_item.hasAttrib("real_end"):
        settings['real_end'] = work_item.floatAttribValue('real_end', 0)
    else:
        settings['real_end'] = settings['end']

    settings['usecustomoutputparm'] = work_item.intAttribValue('usecustomoutputparm')
    settings['outputparm'] = work_item.stringAttribArray('outputparm')
    settings['reloadparm'] = work_item.stringAttribValue('reloadparm')

    if work_item.hasAttrib('executeparm'):
        settings['executeparm'] = work_item.stringAttribValue('executeparm')
    else:
        settings['executeparm'] = "execute"

    if work_item.hasAttrib('ignoreloaderrors'):
        settings['ignoreloaderrors'] = work_item.intAttribValue('ignoreloaderrors')
    else:
        settings['ignoreloaderrors'] = 0

    settings['filetag'] = work_item.stringAttribValue('filetag')
    settings['norange'] = work_item.intAttribValue('norange')
    settings['cookorder'] = work_item.intAttribValue('cookorder')
    settings['singletask'] = work_item.intAttribValue('singletask')

    settings['batch'] = work_item.intAttribValue('batch')

    if settings['batch']:
        if hasattr(work_item, 'batchStart'):
            settings['batchstart'] = work_item.batchStart
        else:
            settings['batchstart'] = 0
        settings['batchpoll'] = work_item.intAttribValue('batchpoll')
    else:
        settings['batchstart'] = 0
        settings['batchpoll'] = False

    settings['dist'] = work_item.intAttribValue('dist')

    if settings['dist']:
        settings['slicex'] = work_item.intAttribValue('slicedivs', 0)
        settings['slicey'] = work_item.intAttribValue('slicedivs', 1)
        settings['slicez'] = work_item.intAttribValue('slicedivs', 2)
        settings['control'] = work_item.stringAttribValue('control')
        settings['trackip'] = work_item.stringAttribValue('trackerhost')
        settings['trackport'] = work_item.intAttribValue('trackerport')
        settings['slice'] = work_item.intAttribValue('slice')
        settings['slicetype'] = work_item.intAttribValue('slicetype')

    try:
        settings['gpus'] = os.environ['HOUDINI_GPU_LIST']
    except KeyError:
        settings['gpus'] = ''

    settings['perfmon'] = work_item.intAttribValue('useperfmon') > 0
    settings['perfmonfile'] = work_item.stringAttribValue('perfmonfile')
    settings['debugfile'] = work_item.stringAttribValue('debugfile')
    settings['reportdebugoutputs'] = \
        work_item.intAttribValue('reportdebugoutputs') > 0

    return settings

def run_job(settings):

    # cache the ip of the result server on the args to speed up rpc calls
    if not settings['server']:
        if not disable_rpc:
            hostname, port = os.environ['PDG_RESULT_SERVER'].split(':')
            settings['server'] = socket.gethostbyname(hostname) + ':' + port
        else:
            settings['server'] = ''

    saved_environ = os.environ.copy()

    # Load the file and apply and distributed sim initialization if needed
    hip_local = localizePath(settings['hip'])

    printlog("Loading .hip file %s." % hip_local)
    if not hq.fileExists(hip_local):
        printlog("Cannot find file %s" % hip_local)
        hou.exit(1)

    # convert to forward slash again to handle any windows-style path components
    # that have been expanded
    hip_local = hip_local.replace('\\', '/')
    try:
        hou.hipFile.load(hip_local, ignore_load_warnings=True)
    except hou.OperationFailed as e:
        if settings['ignoreloaderrors']:
            printlog(
                "WARNING: Error(s) when loading .hip file:\n{}".format(str(e)))
        else:
            raise e

    # re-apply saved env vars which may have been overwritten by those saved in
    # the hip file.
    dont_set = ('HIP', 'HIPFILE', 'HIPNAME', 'JOB', 'PI', 'E', 'POSE', 'status',
        'EYE', 'ACTIVETAKE')
    hscript_set = hou.hscript('set -s')
    for ln in hscript_set[0].split('\n'):
        if ln.startswith('set '):
            split_val = ln[7:].split(' = ')
            if not split_val:
                continue
            key = split_val[0]
            if (key in saved_environ) and (key not in dont_set):
                setcmd = "setenv {} = '{}'".format(key, saved_environ[key])
                hou.hscript(setcmd)

    if settings['sethip']:
        hou.hscript('set HIP={}'.format(settings['sethip']))
        hou.hscript('set HIPFILE={}/{}'.format(settings['sethip'], hip_local))

    hou.hscript('varchange')

    applyChannels(settings['work_item'])

    if settings['dist']:
        divisions = (settings['slicex'], settings['slicey'], settings['slicez'])
        initDistSim(settings['control'], settings['trackip'],
            settings['trackport'], settings['slice'], settings['slicetype'],
            divisions)

    # Check for special-case nodes
    node = hq.getNode(settings['node_path'])
    node_type = node.type().name()

    # Set GPU overrides for Redshift
    if node_type == 'Redshift_ROP' and settings['gpus']:
        gpus = settings['gpus'].split(",")
        gpuSettingString = ""
        for i in range(8):
            if str(i) in gpus:
                gpuSettingString += "1"
            else:
                gpuSettingString += "0"
        hou.hscript("Redshift_setGPU -s " + gpuSettingString)

    if node_type.startswith('baketexture::'):
        cooker = BakeTextureCooker()
    elif node_type.find('sop_simple_baker') >= 0 or node_type.find('rop_games_baker') >= 0:
        cooker = GamesBakerCooker()
    else:
        op_map = {'filecache' : 'render',
                  'vellumio' : 'filecache1/render',
                  'dopio' : 'render',
                  'karma' : 'rop_usdrender'}

        found = False
        for op_name, rop_path in list(op_map.items()):
            if node_type.find(op_name) >= 0 and node.node(rop_path):
                cooker = IndirectRopCooker(rop_path)
                found = True
                break

        if not found:
            cooker = RopCooker()

    return_code = 0
    try:
        # Cook based on the settings supplied by the user
        if settings['batch'] or settings['dist']:
            if not cooker.cookBatchFrames(settings):
                return_code = 1
        else:
            if cooker.cookSingleFrame(settings) is None:
                return_code = 1
    except:
        traceback.print_exc()
        return_code = 1

    if settings['debugfile']:
        local_path = localizePath(settings['debugfile'])
        printlog("Saving debug .hip file to %s" % local_path)

        # Ensure file path exists
        if not os.path.exists(os.path.dirname(local_path)):
            try:
                os.makedirs(os.path.dirname(local_path))
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise

        hou.hipFile.save(local_path)

        if settings['reportdebugoutputs']:
            reportResultData(
                settings['debugfile'],
                result_data_tag='file/hip/debug',
                raise_exc=False)

    hou.exit(return_code)

def main():

    # mantra will exec this file, so we need to detect
    # that case and exit early
    if len(sys.argv) == 1:
        sys.exit(0)

    main_parser = argparse.ArgumentParser()
    sub_parsers = main_parser.add_subparsers(dest='scriptcommand')

    # Command line parser

    parser = sub_parsers.add_parser('args', description="Cooks a ROP node as a single frame job or as a batch")

    # Parameters that all calls must pass through
    # (hip path, node, item name, callback server)
    parser.add_argument('-p', '--hip', dest='hip', default='', required=True,
                        help="The .hip file containing the ROP to cook")
    parser.add_argument('-n', '--node', dest='node_path', default='', required=True,
                        help="The node path within the hip file, e.g. /out/mantra")
    parser.add_argument('-i', '--item', dest='item_name', default='', required=False,
                        help="The work item name for the job")
    parser.add_argument('-s', '--server', dest='server', default='', required=False,
                        help="[DEPRECATED] The callback server address, e.g myhost:45678")

    parser.add_argument('-sh', '--sethip', dest='sethip',
                        help='Override $HIP to this path after loading the hip file')

    # Optional custom output parameter
    parser.add_argument('-pm', '--outputparm', dest='outputparm', default='',
                        help="The name of the parm the specifices the ROP node's output, if any")
    # Optional custom execute button parameter
    parser.add_argument('-pe', '--executeparm', dest='executeparm', default='execute',
                        help="The name of the button parm to press to cook the ROP, if it any")
    # Optional settings for specifying a parent TOP node
    parser.add_argument('-to', '--top', dest='top_path', default='',
                        help="The TOP node that contains the ROP, if any")

    # Optional settings for distributed sims (control, tracker ip, tracker port)
    parser.add_argument('-cn', '--control', dest='control', default='',
                        help="The control DOP node for distributed sims")
    parser.add_argument('-ti', '--trackip', dest='trackip', default='',
                        help="The distributed sim tracker ip")
    parser.add_argument('-tp', '--trackport', dest='trackport', default='',
                        help="The distributed sim tracker port")
    parser.add_argument('-sl', '--slice', dest='slice', default=0, type=int,
                        help="The distribued sim slice number")
    parser.add_argument('-st', '--slicetype', dest='slicetype', default=0,
                        type=int, help="The slice type id")

    # Volume slice settings
    parser.add_argument('-sdx', '--slicex', dest='slicex', default=0, type=int,
                        help="Volume slice x division")
    parser.add_argument('-sdy', '--slicey', dest='slicey', default=0, type=int,
                        help="Volume slice y division")
    parser.add_argument('-sdz', '--slicez', dest='slicez', default=0, type=int,
                        help="Volume slice z division")

    # Frame range parameters (start, stop, inc)
    parser.add_argument('-fs', '--start', dest='start', default=0, type=float,
                        help="The start frame number")
    parser.add_argument('-fe', '--end', dest='end', default=0, type=float,
                        help="The end frame number")
    parser.add_argument('-fi', '--step', dest='inc', default=1, type=float,
                        help="The frame increment")

    # Tile parameters
    parser.add_argument('-tix', '--tileix', dest='tileix', type=int, default=-1,
                        help="The tile index for mantra")

    # Mode selection flags
    parser.add_argument('--batch', dest='batch', action='store_true')
    parser.add_argument('--dist', dest='dist', action='store_true')
    parser.add_argument('--norange', dest='norange', action='store_true')
    parser.add_argument('--singletask', dest='singletask', action='store_true')

    # Batch polling flag, needed for cooking batches that begin when their
    # first dependency is read
    parser.add_argument('--batchpoll', dest='batchpoll', action='store_true')

    # GPU affinity device IDs separted by comma (e.g. --gpus 1,2)
    parser.add_argument('--gpus', dest='gpus', default='', required=False,
                        help="GPU device IDs override for render plugins (comma-separated)")

    # Result data tag override
    parser.add_argument('--tag', dest='tag', default='', required=False,
                        help="The custom result data tag")


    # JSON File Parser

    _ = sub_parsers.add_parser('json', description="Cooks a ROP node "
                               "as a single frame job or as a batch")

    # Service Mode Parser

    service_mode_parser = sub_parsers.add_parser('servicemode',
                                description="Runs the ROP Fetch job script "
                                            " in service mode.")

    service_mode_parser.add_argument('--address', dest='address', required=True)
    service_mode_parser.add_argument('--port', dest='port', required=True)
    service_mode_parser.add_argument('--logfile', dest='logfile')
    service_mode_parser.add_argument('--client_name', dest='client_name', required=True)

    args = main_parser.parse_args()

    file_logging = False

    settings = None

    if args.scriptcommand == "args":
        settings = _parseArgs(args)
    elif args.scriptcommand == "json":
        work_item = WorkItem.fromJobEnvironment(as_native=True)
        applyChannels(work_item)
        settings = _parseWorkItemFile(work_item)
    elif args.scriptcommand == "servicemode":
        if args.logfile:
            file_logging = True
            logfile_path = os.path.expandvars(args.logfile)

            if not os.path.exists(logfile_path):
                try:
                    os.makedirs(os.path.dirname(logfile_path))
                except OSError:
                    if not os.path.isdir(os.path.dirname(logfile_path)):
                        file_logging = False

        if file_logging:
            sys.stdout = open(os.path.expandvars(args.logfile), 'w')
        else:
            sys.stdout = open(os.devnull, 'w')

        printlog("Running Houdini {} with PID {}".format(
            hou.applicationVersionString(), os.getpid()))
        runServiceMode(args)
        sys.exit(0)

    printlog("Running Houdini {} with PID {}".format(
        hou.applicationVersionString(), os.getpid()))

    run_job(settings)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        printlog(traceback.format_exc())
        hou.exit(1)
