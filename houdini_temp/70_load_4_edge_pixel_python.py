import math
node = hou.pwd()
geo = node.geometry()
margin = node.parm('margin').eval()

def dopt(point):
  coppath = point.attribValue('coppath') #Return the value store in this vertex for a particular attribute.
  copnode = hou.node(coppath)
  samplepts = ( (0.5, 1.0-margin), #up
                (1.0-margin, 0.5), #right
                (0.5, margin), #down
                (margin, 0.5) )#left
  samplepixels = []
  for uv in samplepts:
    colorPixel = copnode.getPixelByUV('C', uv[0], uv[1]) #getPixelByUV(plane, u, v)
    r,g,b = map(lambda x: math.floor(x*64), colorPixel)#map(function, iterables)/lambda is anonymous function lambda arguments:expression
    encoded = int(r*64*64+g*64+b)
    samplepixels.append(encoded)
    #print "{} -> {}\n".format(uv, encoded)
    
  point.setAttribValue('colorPixel', samplepixels)

for pt in geo.points():
  dopt(pt)