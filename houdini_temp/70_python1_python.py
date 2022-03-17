from xml.etree.ElementTree import ElementTree
import hou
import os
from xml.etree.ElementTree import parse

node = hou.pwd()
geo = node.geometry()

file_gpx = node.evalParm('gpx')
maxpts = node.evalParm('pts')

def main()
    if not file_gpx : return
    if not file_gpx.endwith('.gpx') : return

    file_path = os.path.abspath(file_gpx)



main()





# Add code to modify contents of geo.
# Use drop down menu to select examples.
