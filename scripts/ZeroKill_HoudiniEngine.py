import json
import os
import re
import sys
from collections import OrderedDict
import hou
import pdg
import datetime
import requests
import hashlib
# encoding=utf8
def ZeroKill_Message(Account,content):

    import time
    
    url = "https://msgcenter.37wan.com/index.php?g=task&m=task&a=add"
    
    key = "e22fb2457b4f493b305ce34bd2d9d7a8"
    
    sys_id = str(26)
    
    time = str(int(time.time()))
    
    task_type = str(1)
    
    receive_list = Account
    
    send_title = 'ZeroKill Offical News'
    
    send_content = content
    
    send_time = dateTime_p = datetime.datetime.now()
    
    str_p = datetime.datetime.strftime(dateTime_p,'%Y-%m-%d %H:%M:%S')
    
    data = {
        "task_type": task_type,
        "receive_list": receive_list,
        "cc_list": "",
        "send_title" : send_title,
        "send_content" : send_content.encode('unicode-escape').decode('string_escape'),
        "send_time" : str_p,
        "key" : key,
        "sys_id" : sys_id,
        "time" : time,
        "sn" : hashlib.md5((key + sys_id + time + task_type + receive_list + send_title + send_content + str_p).encode('utf-8')).hexdigest()
    }
    
    
    
    res = requests.post(url=url,data=data)


hou.setFps(30)

Current_Path = sys.path[0]

newbase_name = os.path.basename(Current_Path)

hip_Path = Current_Path + "/" + newbase_name + ".hip"

hip_Path = hip_Path.replace('\\','/')

Python_FileName = sys.argv[0].split("/")[-1]

Account_python = Python_FileName.split("_")[-1]

AccountList = re.findall(r"\d+\.?\d*",Account_python)

Account = AccountList[0]

hou.hipFile.save(hip_Path)

hou.hda.installFile("V:/ZeroKill_Assotl/"+newbase_name+".hda")

#$HIP
Root_Hip = os.getenv("HIP")
#Create ObjLevel
Root_Obj = hou.node("/obj")

Geometry_HDA 		= Root_Obj.createNode("geo",node_name=newbase_name)

HDA_Node 			= Geometry_HDA.createNode(newbase_name,node_name = newbase_name)

HDA_Property_Value =[]



json_data = open(Root_Hip + "/" + newbase_name + ".json").read()


data = json.loads(json_data,object_pairs_hook=OrderedDict)

Temp_Value        ={}

Temp_Type         ={}

Temp_NumCompents  ={}

Json_Value_Data	  =[]

for i in sorted(data.keys(),key=int):

    Temp_Value[i] = data[i]['Value']
    
    Temp_Type[i]  = data[i]['Type']
    
    Temp_NumCompents[i] = data[i]['NumComponents']

    #print(Temp_NumCompents[i])
    
    if Temp_Type[i].encode("utf-8") == "Label":
        
        continue

    if  int(Temp_NumCompents[i].encode("utf-8"))>1:
        
        Value_List = Temp_Value[i].split('_')

        for item in Value_List:

            Json_Value_Data.append(item.encode("utf-8"))

    else:
    
        Json_Value_Data.append(Temp_Value[i].encode("utf-8"))

HdaParms_Group = HDA_Node.parmTemplateGroup()

HdaNode_ParmsType = HdaParms_Group.parmTemplates()

New_DataIndex = 0

for parm in HdaNode_ParmsType:

    Data_Type = str(parm.type().name())

    Data_Name = str(parm.name())

    NumComponents = parm.numComponents();

    print(Data_Type)

    #print(str(parm.name()))
    
    if Data_Type == "Label":

        
        continue

    #if Data_Type=="String":
    if Data_Type == "String":

        HDA_Node.parm(Data_Name).set(Json_Value_Data[New_DataIndex])

        New_DataIndex += 1

        continue

    if Data_Type == "Int":
        
        HDA_Node.parm(Data_Name).set(Json_Value_Data[New_DataIndex])

        New_DataIndex += 1
        continue
    
    if Data_Type == "Toggle":

        HDA_Node.parm(Data_Name).set(Json_Value_Data[New_DataIndex])

        New_DataIndex += 1
        continue

    if Data_Type == "Float" and NumComponents==1:
        print(New_DataIndex)
        HDA_Node.parm(Data_Name).set(Json_Value_Data[New_DataIndex])

        New_DataIndex += 1
        
        continue

    if Data_Type == "Float" and NumComponents==3:

        HDA_Node.parm(Data_Name+"x").set(Json_Value_Data[New_DataIndex])

        HDA_Node.parm(Data_Name+"y").set(Json_Value_Data[New_DataIndex+1])

        HDA_Node.parm(Data_Name+"z").set(Json_Value_Data[New_DataIndex+2])

        New_DataIndex+=3

        continue
    
    if Data_Type == "Float" and NumComponents==2:

        HDA_Node.parm(Data_Name+"x").set(Json_Value_Data[New_DataIndex])

        HDA_Node.parm(Data_Name+"y").set(Json_Value_Data[New_DataIndex+1])

        New_DataIndex+=2

        continue

HDA_Node.parm("execute").pressButton()

Temp_Task = newbase_name + ' Already Finish'

ZeroKill_Message(Account,Temp_Task)

hou.hipFile.save(hip_Path)




    













