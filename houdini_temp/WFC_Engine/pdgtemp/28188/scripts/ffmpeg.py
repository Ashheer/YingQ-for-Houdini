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
# NAME:	        ffmpeg.py ( Python )
#
# COMMENTS:     Wrapper script for executing ffmpeg commands and reporting the results back to PDG
#
#               Note: This module is intended to not have any dependency on a Houdini installation.

from __future__ import print_function, absolute_import

import argparse
import sys, os
import traceback
import glob
import re

try:
    from pdgcmd import localizePath, makeDirSafe, execCommand
    from pdgjson import WorkItem
except ImportError:
    from pdgjob.pdgcmd import localizePath, makeDirSafe
    from pdgjob.pdgjson import WorkItem

FFMPEG_TOOL_NAME = 'ffmpeg'

def sanitizeEnvVars():
    """Clear out Houdini-related paths from PATH and LD_LIBRARY_PATH.

    Only clear these out on non-Windows platforms."""
    if sys.platform.startswith("win"):
        return

    hfs = os.environ.get("HFS")
    if not hfs:
        return

    TO_SANITIZE = ("LD_LIBRARY_PATH", "PATH")

    for var_name in TO_SANITIZE:
        paths = os.environ.get(var_name, "").split(":")
        sanitized_paths = [
            path for path in paths if hfs not in path
        ]
        sanitized_value = ":".join(sanitized_paths)
        os.environ[var_name] = sanitized_value

def extractimages(work_item):

    ffmpegcommand = work_item.stringAttribValue('ffmpegcommand')
    outputfiles = work_item.fileAttribValue('outputfiles')

    if not outputfiles or not outputfiles.local_path:
        work_item.addError("Output File Pattern must be specified.", fail_task=True)

    output_dir = os.path.dirname(outputfiles.local_path)
    makeDirSafe(output_dir)

    ffmpegcommand_localized = os.path.expandvars(localizePath(ffmpegcommand))  

    execCommand(ffmpegcommand_localized, FFMPEG_TOOL_NAME)

    glob_pattern = re.sub(r'\%[0-9][0-9].', '*', outputfiles.local_path)
    globbed_files = sorted(glob.glob(glob_pattern))

    for result_file in globbed_files:
        work_item.addResultData(result_file, outputfiles.tag)

def encodevideo(work_item):

    ffmpegcommand = work_item.stringAttribValue('ffmpegcommand')
    outputfile = work_item.fileAttribValue('outputfile')
    framelistfile = work_item.fileAttribValue('framelistfile')
    inputsource = work_item.intAttribValue('inputsource')

    if not outputfile or not outputfile.local_path:
        work_item.addError("Output File must be specified.", fail_task=True)

    output_dir = os.path.dirname(outputfile.local_path)
    makeDirSafe(output_dir)

    if inputsource == 0:
        if not framelistfile or not framelistfile.local_path:
            work_item.addError("Framelist File must be specified.", fail_task=True)

        framelist_dir = os.path.dirname(framelistfile.local_path)
        makeDirSafe(framelist_dir)

        inputfiletag = work_item.stringAttribValue('inputfiletag')
        inputfiles = work_item.inputResultDataForTag(inputfiletag)

        if not inputfiles or len(inputfiles) < 1:
            work_item.addError("No input files were provided", fail_task=True)

        with open(framelistfile.local_path, 'w') as framefile:
            for image in inputfiles:
                # We only want "real" results.
                # Skip any "expected" results.
                if image.type == 1:
                    continue

                if os.name == 'nt':
                    try:
                        framepath = os.path.relpath(image.local_path,
                                                    framelist_dir)
                    except ValueError:
                        work_item.addError(
                            "The framelist file must be located on the same "
                            "drive as the input images. The location of the "
                            "framelist file can be changed with the Frame "
                            "List File parameter.", fail_task=True)
                    framepath = framepath.replace("\\", "/")
                else:
                    framepath = image.local_path

                framefile.write('file \'{}\'\n'.format(framepath))

    ffmpegcommand_localized = os.path.expandvars(localizePath(ffmpegcommand))

    execCommand(ffmpegcommand_localized, FFMPEG_TOOL_NAME)

    work_item.addResultData(outputfile.local_path, outputfile.tag)

def main():
    workitem = WorkItem.fromJobEnvironment()
    op = workitem.stringAttribValue('operation')

    if op == 'extract':
        extractimages(workitem)
    elif op == 'encode':
        encodevideo(workitem)
    else:
        work_item.addError("No operation provided", fail_task=True)

if __name__ == "__main__":
    try:
        sanitizeEnvVars()
        main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(-1)
