"""Run futurize on a set of python files and HDAs.

This script is given a list containing python files, expanded HDA
directories, and/or HDA files. It will run futurize on the python files,
and on the python in the HDAs. By default, it will create new
files/directories that are copies of the originals, and convert the code
there. The copies will be named with the suffix '_converted'.

Certain futurize transformations are skipped by default which would
otherwise introduce imports from modules other than __future__.
"""
from __future__ import print_function

import subprocess
import argparse
import os
import sys
import fnmatch
import re
import tempfile
import shutil

try:
    import hou
except ImportError:
    print('Error: py23convert must be run through hython.')
    sys.exit(1)

try:
    from collections.abc import Iterable
except ImportError:
    from collections import Iterable

from expandHDA import expand
from collapseHDA import collapse

if sys.version_info.major < 3:
    print('Error: py23convert must be run in a Python 3 build of Houdini')
    sys.exit(1)

# The futurize transformations that are skipped.
FIXES_TO_SKIP = [
    'libfuturize.fixes.fix_basestring',
    'libfuturize.fixes.fix_cmp',
    'libfuturize.fixes.fix_division_safe',
    'libfuturize.fixes.fix_execfile',
    'libfuturize.fixes.fix_future_builtins',
    'libfuturize.fixes.fix_future_standard_library',
    'libfuturize.fixes.fix_future_standard_library_urllib',
    'libfuturize.fixes.fix_metaclass',
    'libfuturize.fixes.fix_object',
    'libfuturize.fixes.fix_xrange_with_import',
    'libpasteurize.fixes.fix_newstyle',
]

if sys.platform == 'win32':
    platform = 'Windows'
elif sys.platform.startswith('linux'):
    platform = 'Linux'
elif sys.platform == 'darwin':
    platform = 'Mac'
else:
    raise OSError('Unsupported platform: %s' % sys.platform)

def expressionReturnsTuple(template):
    return isinstance(template.defaultExpressionLanguage(), Iterable)

def convertDefaultExpressions(args, template):
    def_expr_lang = template.defaultExpressionLanguage()
    def_expr      = template.defaultExpression()
    exprs = []

    if expressionReturnsTuple(template):
        exprs = list(zip(def_expr_lang, def_expr))
    else:
        exprs = [(def_expr_lang, def_expr)]

    converted_exprs = []

    for expr in exprs:
        lang, code = expr

        if lang == hou.scriptLanguage.Python and code:
            print('Converting python default expression')

            converted_code = convertSnippet(args, code)

            if converted_code:
                converted_exprs.append((lang, converted_code))
            else:
                converted_exprs.append((lang, code))
        else:
            converted_exprs.append((lang, code))

    if not args['diff_only']:
        if expressionReturnsTuple(template):
            template.setDefaultExpression([expr for _, expr in converted_exprs])
        else:
            template.setDefaultExpression(converted_exprs[0][1])

def convertCallbackScript(args, template):
    if template.scriptCallbackLanguage() != hou.scriptLanguage.Python:
        return

    cb = template.scriptCallback()

    if not cb:
        return

    print('Converting python callback script')

    converted_cb = convertSnippet(args, cb)

    if not args['diff_only'] and converted_cb:
        template.setScriptCallback(converted_cb)

def convertMenuScript(args, template):
    if template.itemGeneratorScriptLanguage() != hou.scriptLanguage.Python:
        return

    script = template.itemGeneratorScript()

    if not script:
        return

    print('Converting python menu script')

    converted_script = convertSnippet(args, script)

    if not args['diff_only'] and converted_script:
        template.setItemGeneratorScript(converted_script)

def convertSnippet(args, snippet):
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, 'py23c-snippet')

    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    try:
        converted = ''

        with open(tmp_path, 'w') as fp:
            fp.write(snippet)

        cmd = getFuturizeCmd()

        the_cmd = cmd.split()

        if not args['fix_all']:
            for f in FIXES_TO_SKIP:
                the_cmd.append('-x')
                the_cmd.append(f)

        if not args['verbose'] and not args['diff_only']:
            # --no-diffs tells futurize to not show diffs of the
            # refactoring.
            the_cmd.append('--no-diffs')

        # -w causes the modifications to be written to the files.
        # -n tells futurize to not create backups of the modified files.
        the_cmd.extend(['-w', '-n'])

        this_cmd = list(the_cmd)
        this_cmd.append(tmp_path)

        if not args['diff_only'] and args['fix_tabs']:
            fixTabs([tmp_path], args['spaces_per_tab'])

        runCmd(args, this_cmd)

        with open(tmp_path, 'r') as fp:
            converted = fp.read()

        # Eat this import as it is already imported for you in an expression
        converted = converted.replace('from __future__ import print_function\n', '')
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return converted

def convertHDAScripts(args, hda_src_path, hda_dest_path):
    hou.hda.installFile(hda_src_path)

    try:
        hda_defs = hou.hda.definitionsInFile(hda_src_path)

        for hda_def in hda_defs:
            parm_template_grp = hda_def.parmTemplateGroup()
            parm_templates = parm_template_grp.parmTemplates()

            new_parm_template_grp = hou.ParmTemplateGroup()

            for template in parm_templates:
                if hasattr(template, 'defaultExpression'):
                    convertDefaultExpressions(args, template)

                if hasattr(template, 'scriptCallback'):
                    convertCallbackScript(args, template)

                if hasattr(template, 'itemGeneratorScript'):
                    convertMenuScript(args, template)

                new_parm_template_grp.append(template)

            hda_def.setParmTemplateGroup(new_parm_template_grp)

            hda_def.save(hda_dest_path, create_backup=False)
    finally:
        hou.hda.uninstallFile(hda_src_path)

def convert(args, fp=None):
    """Convert the files given in the args dictionary using futurize.

    For details on what the dictionary args must contain, see the
    ArgumentParser used in the main method of this file (at the end of this file).
    """
    # fp is a file descriptor that can be used to redirect the output of the 
    # conversion process
    args['fp'] = fp

    if args['fix_tabs'] and args['spaces_per_tab'] < 1:
        exit('Error: TAB_WIDTH must be >= 1')
    files = getListOfFiles(args['files'],
                           args['in_place_edit'] or args['diff_only'],
                           args['sort_hda'])

    cmd = getFuturizeCmd()

    # The conversion tool to run, and the fixes being skipped.
    tool = 'futurize'

    my_fixes_to_skip = FIXES_TO_SKIP.copy()

    if args['fix_all']:
        my_fixes_to_skip = []

    # Whether we need to print a newline before the next print.
    need_newline = False

    if args['list_fixes']:
        # Print the list of transformations available for the conversion
        # tool.
        the_cmd = cmd + ' -l'
        runCmd(args, the_cmd.split(' '))
        print('fix_tabs')
        if len(my_fixes_to_skip) > 0:
            print('\nThe following transformations are skipped:')
            for f in my_fixes_to_skip:
                print(f)
            if not args['fix_tabs']:
                print('fix_tabs')
        need_newline = True

    the_cmd = cmd.split()

    the_cmd.extend(['-j', str(args['processes'])])

    for f in my_fixes_to_skip:
        the_cmd.append('-x')
        the_cmd.append(f)

    if not args['verbose'] and not args['diff_only']:
        # --no-diffs tells futurize to not show diffs of the
        # refactoring.
        the_cmd.append('--no-diffs')

    expl_str = "Showing changes for "
    if not args['diff_only']:
        # -w causes the modifications to be written to the files.
        # -n tells futurize to not create backups of the modified files.
        the_cmd.extend(['-w', '-n'])
        expl_str = "Converting "

    if len(files['python']) > 0:
        if need_newline:
            print('')
        print(expl_str + 'provided python files')
        this_cmd = list(the_cmd)
        this_cmd.extend(files['python'])
        if not args['diff_only'] and args['fix_tabs']:
            fixTabs(files['python'], args['spaces_per_tab'])
        runCmd(args, this_cmd)
        need_newline = True

    for ed in files['expanded_dirs']:
        if need_newline:
            print('')
        print(expl_str + 'provided expanded HDA directory: ' + ed['dir'])
        if len(ed['files']) == 0:
            print("No python files to convert.")
        else:
            this_cmd = list(the_cmd)
            this_cmd.extend(ed['files'])
            if not args['diff_only'] and args['fix_tabs']:
                fixTabs(ed['files'], args['spaces_per_tab'], ed['dir'])
            runCmd(args, this_cmd, cwd=ed['dir'])

        need_newline = True

    for hda in files['hdas']:
        if need_newline:
            print('')
        print(expl_str + 'provided HDA: ' + hda['hda'])
        if len(hda['files']) == 0:
            #print("No files to convert.")
            pass
        else:
            this_cmd = list(the_cmd)
            this_cmd.extend(hda['files'])
            if not args['diff_only'] and args['fix_tabs']:
                fixTabs(hda['files'], args['spaces_per_tab'], hda['dir'])
            runCmd(args, this_cmd, cwd=hda['dir'])
        need_newline = True

        # Get the name of the new converted HDA.
        if args['in_place_edit']:
            name = hda['hda']
        else:
            name = getConvertedName(hda['hda'])

        # Collapse the expanded directory we created into the converted
        # HDA, and delete the expanded directory.
        if not args['diff_only']:
            collapse({'dir': hda['dir'], 'file': name, 'delete_dir': True})

        convertHDAScripts(args, hda['hda'], name)

        if not args['diff_only']:
            print("The converted HDA is " + name)

def getListOfFiles(input_list, in_place_edit = False, sort = False):
    """Return a dictionary containg all python files to convert from the
    input list of files/directories.

    This function will expand each provided HDA into a temporary
    directory, which are part of the return value. It is the
    responsibility of the caller of this function to cleanup that
    directory if necessary.

    If in_place_edit is False, this function will create copies of all
    the python files and expanded HDA directories from input_list, and
    return the files to convert from the copies. If in_place_edit is
    True, no copies are made and the original files are returned to be
    converted.

    The return value is a dictionary with the following format:
    {'python': [python files from input_list],
     'expanded_dirs': [{'dir': dir_name,
                        'files': [files to convert in dir_name]}],
     'hdas': [{'hda': hda_name,
               'dir': expanded_hda_dir,
               'files': [files to convert in expanded_hda_dir]}]
    }
    where expanded_dirs contains the directories given in the input
    list, expanded_hda_dir is the directory this function created when
    expanding the HDA, and all the files in expanded_dirs and hdas are
    relative to the expanded directory containing them.
    """
    files = {'python': [], 'expanded_dirs': [], 'hdas': []}
    for f in input_list:
        if os.path.isfile(f):
            if isHDAFile(f):
                # f is an HDA, so we expand it, find the files to
                # convert in it, and add them to the list.
                dir_name = tempfile.mkdtemp()
                expand({'file': f, 'dir': dir_name})
                files['hdas'].append({'hda': f, 'dir': dir_name,
                                      'files': findPythonFilesInDir(dir_name,
                                                                    sort)})
            else:
                # f is not an HDA file, so its a python file.
                f_con = f
                if not in_place_edit:
                    f_con = getConvertedName(f)
                    print('Copying {} to {}'.format(f, f_con))
                    shutil.copy(f, f_con)
                files['python'].append(f_con)
        elif os.path.isdir(f):
            # f is an expanded HDA directory, so we find the files to
            # convert in it, and add them to the list.
            realpath = os.path.realpath(f)
            if realpath[-1] == os.sep:
                print('Error: Got a root directory: ' + f
                      + '. It will be ignored.')
                continue
            f_con = f
            if not in_place_edit:
                # To get the converted name, f must actually name the
                # directory, it cannot be referenced by '.', '..', or
                # something like 'C:'. Also f cannot end in os.sep.
                f = f.rstrip(os.sep)
                head, tail = os.path.split(f)
                if (tail == '' and head[-1] == ':') or tail in ['.', '..']:
                    f = realpath
                f_con = getConvertedName(f)
                print('Copying {} to {}'.format(f, f_con))
                if os.path.exists(f_con):
                    shutil.rmtree(f_con)
                shutil.copytree(f, f_con)
            files['expanded_dirs'].append(
                {'dir': f_con,
                 'files': findPythonFilesInDir(f_con, sort)})
        else:
            # f is neither a directory or file.
            print('Error: Got something that is neither a file nor a \
directory: ' + f + '. It will be ignored.')
    return files

def findPythonFilesInDir(d, sort = False):
    """Return the list of all python files in d to be converted.

    d is an expanded HDA directory.
    All files in the returned list are relative to d.
    """
    files = []
    tree = os.walk(d)
    # dire is a 3-tuple (dir, subdirs, files) where dir is a directory
    # name, subdirs is a list of all immediate subdirectories of dir,
    # and files is a list of all files in dir.
    # Through the for loop, we go through every such 3-tuple where dir
    # is d or some subdirectory of d.
    for dire in tree:
        if sort:
            # We sort the list of subdirs and this list of files so that the
            # traversal order is consistent. This is only to aid testing, by
            # making output consistent.
            dire[1].sort()
            dire[2].sort()

        if len(dire[2]) == 0:
            continue

        # The list of files will be relative to d. prefix gives the
        # relative path to the files.
        if platform == 'Windows':
            prefix = os.path.relpath(os.path.join(dire[0], dire[2][0]), d)
            prefix = prefix[0 : len(prefix) - len(dire[2][0])]
        else:
            if dire[0] == d:
                prefix = ''
            elif d[len(d) - 1] == os.sep:
                prefix = dire[0][len(d):] + os.sep
            else:
                prefix = dire[0][len(d) + 1:] + os.sep
        # Add every .py file in the "current directory".
        files.extend([prefix +
            f for f in [x for x in dire[2] if fnmatch.fnmatch(x, '*.py')]])
        # ExtraFileOptions is a special file found in expanded HDAs
        # which we use to find script files that are in python. We find
        # all such files and add them to our list.
        if 'ExtraFileOptions' in dire[2]:
            if platform == 'Windows' and dire[0][len(dire[0]) - 1] == ':':
                directory = dire[0]
            else:
                directory = dire[0] + os.sep
            files.extend(parseExtraFileOptions(directory, prefix))
    return files

def parseExtraFileOptions(d, prefix):
    """Return python script files in directory d by parsing the
    ExtraFileOptions file in d.

    d + 'ExtraFileOptions' must be the path to the ExtraFileOptions
    file. This function also searches for a few specific files that are
    known to sometimes be in HDAs in the same directory as
    ExtraFileOptions. File names returned are prefixed by prefix.
    """
    files = []
    with open(d + 'ExtraFileOptions', 'r') as fo:
        lines = [l.rstrip() for l in fo.readlines()]

        # The parsing states.
        START = 0
        FILE_SETTINGS = 1
        FILE_SETTINGS_VALUE = 2
        END = 3

        state = START
        # The current "candidate" python file.
        cur_file = None

        for l in lines:
            if state == START and l == '{':
                state = FILE_SETTINGS
            elif state == FILE_SETTINGS and l == '}':
                state = END
                break
            elif state == FILE_SETTINGS:
                # If this is a match, then the filename between the ()
                # has a python setting, so we will check its value.
                match = re.match('\t"(.+)/IsPython":{$', l)
                if match is None:
                    continue

                state = FILE_SETTINGS_VALUE
                cur_file = match.group(1)

            elif state == FILE_SETTINGS_VALUE:
                # We are in a portion of the file giving the IsPython
                # setting for some file.

                # If we get to the end of the portion, we assume the
                # file is not python.
                if re.match('\t},?$', l):
                    state = FILE_SETTINGS
                    cur_file = None
                    continue

                if not l == '\t\t"value":true':
                    continue

                if cur_file:
                    # At this point cur_file is a python file, so we add
                    # it to the list.
                    files.append(prefix + cur_file)

                state = FILE_SETTINGS
                cur_file = None

    # The directory containing the ExtraFileOptions file may also
    # contain additional python files.
    additional_files = [
        'PythonCook',
    ]
    for f in additional_files:
        if os.path.isfile(d + f):
            files.append(prefix + f)

    return files

def getFuturizeCmd():
    """Return the command to run the futurize script.

    We give the -u option to the python binary, which will cause
    futurize's output to not be buffered. This is necessary to ensure
    the correct order of the output from futurize when this script's
    output is redirected.
    """
    if not 'HFS' in os.environ:
        exit('Error: Environment variable HFS is not set')


    hfs = os.environ["HFS"]
    py_ver_major = sys.version_info.major
    py_ver_minor = sys.version_info.minor

    if platform == 'Linux':
        py_root_path = "{}/python".format(hfs)

        cmd = \
            "{}/bin/python{}.{} -u {}/bin/futurize".format(
                py_root_path, py_ver_major, py_ver_minor, py_root_path)

    elif platform == 'Mac':
        # Python root path in developer environment.
        py_root_path = "{}/Frameworks/Python.framework".format(hfs)

        if not os.path.exists(py_root_path):
            # Python root path in end-user environment.
            py_root_path = \
                "{}/../../../../Python.framework".format(hfs)

        py_root_path += "/Versions/{}.{}".format(py_ver_major, py_ver_minor)

        cmd = \
            "{}/bin/python{}.{} -u {}/bin/futurize".format(
                py_root_path, py_ver_major, py_ver_minor, py_root_path)

    elif platform == 'Windows':
        py_root_path = \
            "{}/python{}{}".format(hfs, py_ver_major, py_ver_minor)

        cmd = \
            "{}\\python{}.{}.exe -u {}\\Scripts\\futurize-script.py".format(
                py_root_path, py_ver_major, py_ver_minor, py_root_path)

    return cmd


def isHDAFile(file_name):
    """Return whether the given file is an HDA file.

    That is, return whether file_name is in the format '<name>.hda'.
    """
    root, ext = os.path.splitext(file_name)
    return ext == '.hda'

def getConvertedName(f):
    """Return the file or directory name to use for the converted
    version of f, which is either a python file, HDA file or an expanded
    HDA directory.

    f should be the original HDA or python file name, or the expanded
    HDA directory name. If it is a directory, f cannot end in os.sep,
    and must end in the actual directory name (eg it cannot be
    '/home/prisms/hdas/', 'C:', '/', '.', or '../..'. It can be
    '../../my.hda').
    """
    if os.path.isdir(f):
        # We assume f is an expanded HDA directory.
        return f + '_converted'
    elif os.path.isfile(f):
        # f is either an hda file or a python file.
        root, ext = os.path.splitext(f)
        return root + '_converted' + ext
    else:
        # f is neither a file nor a directory.
        return None

def runCmd(prgargs, *args,**kwargs):
    """A wrapper around subprocess.call."""
    # We flush the buffer before running subprocess.call so that
    # anything in the buffer from this script appears in the output
    # before the output of the subprocess.
    sys.stdout.flush()

    # Redirect the output if specified
    fp = prgargs['fp']

    if fp:
        return subprocess.call(*args, **kwargs, stdout=fp, stderr=fp)
    else:
        return subprocess.call(*args, **kwargs)

def fixTabs(files, tab_width, cwd = ''):
    for filename in files:
        print("Changing tabs to spaces in: " + filename)
        fd, tmpfilename = tempfile.mkstemp()
        os.close(fd)
        filename = os.path.join(cwd, filename)
        with open(tmpfilename, 'w') as tf, open(filename) as f:
            for line in f.readlines():
                l = ''
                num_chars = 0
                for idx, c in enumerate(line):
                    if c == ' ':
                        l += c
                        num_chars += 1
                    elif c == '\t':
                        this_tab_width = tab_width - num_chars % tab_width
                        l += ' ' * this_tab_width
                        num_chars += this_tab_width
                    else:
                        l += line[idx:]
                        break
                tf.write(l)
        shutil.copyfile(tmpfilename, filename)
        os.remove(tmpfilename)

def main():
    parser = argparse.ArgumentParser(description=
        '''Convert python in HDAs and/or python files from python 2 to
        python 2/3 using futurize. This script can accept python files,
        expanded HDA directories, or HDA files. HDA files must be in the
        format <name>.hda. By default, copies are made of the input
        files/directories, and the copies are converted. Files are
        copied with '_converted' added just before the last '.' in the
        file name.  Directories are copied with '_converted' as a
        suffix. Several futurize transformations are skipped by default
        to avoid adding imports from modules other than __future__. The
        script also has an additional optional transformation available
        which will convert tabs in leading whitespace to spaces.''')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show the diffs of the changes being made.')

    parser.add_argument('-d', '--diff-only', action='store_true',
                        help='Only show the diffs of the changes that would be\
                              made, do not actually make any changes.')

    parser.add_argument('-i', '--in-place-edit', action='store_true',
                        help='Overwrite the source files/directories with the\
                              converted versions.')

    parser.add_argument('-l', '--list-fixes', action='store_true',
                        help='List available transformations. Note that\
                              several transformations are skipped to avoid\
                              adding imports from modules other than\
                              __future__.')

    parser.add_argument('-a', '--fix-all', action='store_true',
                        help='Run all futurize transformations. Note that this\
                              may introduce imports from modules other than\
                              __future__.')

    parser.add_argument('-t', '--fix-tabs', action='store_true',
                        help='Replace all tabs used in leading whitespace with\
                              spaces. Tabs are replaced from left to right by\
                              1 to TAB_WIDTH spaces such that the number\
                              of characters up to and including the\
                              replacement is a multiple of TAB_WIDTH. By\
                              default, TAB_WIDTH is 8, but can be set using\
                              the --spaces-per-tab option.')

    parser.add_argument('-s', '--spaces-per-tab', type=int, default=8,
                        metavar='TAB_WIDTH',
                        help='Set the number of spaces that one tab is equal\
                              to. This value is used by the optional fixer\
                              specified by --fix-tabs. By default TAB_WIDTH\
                              is 8.')

    parser.add_argument('-j', '--processes', type=int, default=4,
                        metavar='N_PROCESSES',
                        help='Run n fixers concurrently. By default N_PROCESSES\
                              is 4')

    parser.add_argument('files', metavar='directory|file', nargs='*',
                        help='The expanded HDA directory or HDA file or python\
                              file to convert.''')

    # This option will cause the script to traverse expanded HDA
    # directories in alphabetical order. This is unnecessary for the
    # purposes of the script, and is really only to maintain a
    # consistent output for testing, so we suppress the option from the
    # help info.
    parser.add_argument('--sort-hda', action='store_true',
                        help=argparse.SUPPRESS)

    # vars() converts the result from an object with the arguments
    # stored as attributes, to a dictionary.
    # The long version of an argument becomes the key, with leading
    # hyphens dropped and other hyphens converted to underscores.
    # For example, the value of the '--diff-only' argument is stored in
    # args['diff_only']
    args = vars(parser.parse_args())
    convert(args)

if __name__ == '__main__':
    main()
