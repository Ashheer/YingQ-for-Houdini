node = hou.pwd()
geo = node.geometry()
add_paths  = node.node('../add_paths')

imgdir = node.parm('imagedir').eval()
ext = node.parm('ext').eval()
irang = node.parmTuple('range').eval()

import os

copnet = node.node('../copnet')
copnet.deleteItems(copnet.children())

matnet = node.node('../matnet')
matnet.deleteItems(matnet.children())

objnet = node.node('../objnet')
objnet.deleteItems(objnet.children())

code = "int pt;\n"
for i in range(irang[0], irang[1]+1):
  filenode=copnet.createNode('file', 'img_'+str(i))
  fn = os.path.join(imgdir, str(i)+ext)
  filenode.parm('filename1').set(fn)
  filenode.moveToGoodPosition()
  
  matnode =matnet.createNode('principledshader::2.0', 'mat_'+str(i))
  matnode.parm('basecolor_useTexture').set(1)
  matnode.parm('basecolorr').set(1)
  matnode.parm('basecolorg').set(1)
  matnode.parm('basecolorb').set(1)
  matnode.parm('basecolor_texture').set('op:'+filenode.path())
  matnode.moveToGoodPosition()
  
  geo = objnet.createNode('geo', 'tile_'+str(i))
  grid = geo.createNode('grid')
  grid.parm('sizex').set(1)
  grid.parm('sizey').set(1)
  grid.parm('rows').set(2)
  grid.parm('cols').set(2)
  uv = geo.createNode('uvunwrap')
  uv.parm('spacing').set(0)
  uv.setInput(0, grid)
  mat = geo.createNode('material')
  mat.parm('shop_materialpath1').set(matnode.path())
  mat.setInput(0, uv)
  mat.setDisplayFlag(True)
  mat.setRenderFlag(True)
  grid.moveToGoodPosition()
  uv.moveToGoodPosition()
  mat.moveToGoodPosition()
  geo.moveToGoodPosition()
  
  line  = "pt = addpoint(0, {0,0,0});\n";
  line += "setpointattrib(0, 'path', pt, '{}');\n".format(fn)
  line += "setpointattrib(0, 'coppath', pt, '{}');\n".format(filenode.path())
  line += "setpointattrib(0, 'soppath', pt, '{}');\n".format(geo.path())
  code += line
  
add_paths.parm('snippet').set(code)
