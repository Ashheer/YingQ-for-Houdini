node = hou.pwd() #a shortcut for writing hou.node(".") current node
geo = node.geometry()# SOP network
dimension = map(int, 
               hou.node('../alloc_rule_cube').
               parmTuple('dimension').eval()
            )
N = dimension[0]

import numpy as np

rules=np.zeros(dimension,dtype=int)#return a new array of given shape and type, filled with zeros

given_geo = node.inputs()[1].geometry() # load_4_edge_pixel as input, second input
given=np.zeros((N, 4),dtype=int)
i = 0
for pt in given_geo.points():
  given[i:]=pt.attribValue('colorPixel')
  print(pt.attribValue('colorPixel'))
  i+=1

for i in range(N):
  for j in range(N):
    rules[i][j][0] = given[i][0] == given[j][2] ## j above i
    rules[i][j][1] = given[i][1] == given[j][3] ## i j
    rules[i][j][2] = given[i][2] == given[j][0] ## j below i
    rules[i][j][3] = given[i][3] == given[j][1] ## j i
    for k in range(4):
      geo.point(i*N*4+j*4+k).setAttribValue('islegal', rules[i][j][k])
    