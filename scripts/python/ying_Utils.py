

import hou
import os
import PySide2



# import fee_Utils
# from importlib import reload
# reload(fee_Utils)

def displayConfirmation(prevText='', postText=''):
    sureToRunThis = hou.ui.displayConfirmation(prevText + '\n\nAre you sure you wantS to run this\n你是否确定要运行这个工具\n\n' + postText)
    if not sureToRunThis:
        raise SystemExit('Stop Run', sureToRunThis)

def readTXTAsList(outList, txt):
    for line in txt.readlines():
        curline = line.strip()
        if curline == '':
            continue
        outList.append(curline[:])


def isFloat(inputString):
    if not isinstance(inputString, str):
        raise ValueError()
    
    try:
        returnVal = float(inputString)
        return True
    except:
        return False
    return None







def setClipboardText(text):
    clipboard = PySide2.QtWidgets.QApplication.clipboard()
    clipboard.clear()
    clipboard.setText(text)


def createAndRunBat(command, batPath):
    with open(batPath, 'w') as BAT:
        BAT.write(command)
    os.system(batPath)

def createAndRunBat_HoudiniTEMP(command, batName):
    TEMP_path = hou.getenv('TEMP')
    batPath = TEMP_path + '/' + batName
    createAndRunBat(command, batPath)

def getAbsPath(path):
    if not isinstance(path, str):
        raise TypeError('path must be str', path)

    import os

    if not os.path.isdir(path):
        raise ValueError('path is not dir', path)

    return os.path.abspath(path).replace('\\', '/')



def gitPullByBat(repositoryPath, reloadAllFiles = False):
    absRepositoryPath = getAbsPath(repositoryPath)

    command = repositoryPath[:2] + '\n'
    command += 'cd ' + repositoryPath + '\n'
    command += 'git pull'
    #print(command)

    createAndRunBat_HoudiniTEMP(command, 'feE_Utils_GitPull.bat')

    if reloadAllFiles:
        hou.hda.reloadAllFiles(rescan = True)
        #hou.hda.reloadNamespaceOrder()

    '''
    try:
        from git.repo import Repo
        repo = Repo(repositoryPath)
        repo.git.pull(repositoryPath)
    except:
        pass
    '''
    #os.system('D:/Houdini/FeEProjectHoudini/otls/gitpull.bat')
    #os.system('D:; cd D:/Git/houdini_toolkit; git pull')

def gitPushByBat(repositoryPath):
    absRepositoryPath = getAbsPath(repositoryPath)
    #print(absRepositoryPath)

    command = absRepositoryPath[:2] + '\n'
    command += 'cd ' + absRepositoryPath + '\n'
    command += r'''
git pull
git status

set /p commitComment=
:: if [%commitComment%]==[] ( xxxxx ) else ( xxxx )

git add .

git commit -m "%commitComment%"

git status

:: pause
git push
    '''
    #print(command)
    createAndRunBat_HoudiniTEMP(command, 'feE_Utils_GitPush.bat')

    # if reloadAllFiles:
    #     hou.hda.reloadAllFiles(rescan = True)
    #     #hou.hda.reloadNamespaceOrder()

    
    # import subprocess

    # os.chdir('D:/Houdini/FeEProjectHoudini/otls/')
    # p = subprocess.Popen("cmd.exe /c D:/Houdini/FeEProjectHoudini/otls/gitpush.bat", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # curline = p.stdout.readline()
    # while (curline != b''):
    #     print(curline)
    #     curline = p.stdout.readline()
    # p.wait()
    # print(p.returncode)

