node = hou.pwd() #a shortcut for writing hou.node(".") current node
geo = node.geometry()# SOP network
dimension = list(map(int, 
               hou.node('../alloc_rule_cube').
               parmTuple('dimension').eval()
            )) #convert the second input into interger
print(dimension) #[15,15,6]
N = dimension[0] #15

import numpy as np

rules=np.zeros(dimension,dtype=int)#return a new array of given shape and type, filled with zeros

given_geo = node.inputs()[1].geometry() # load second input, "tileset"
given=np.zeros((N, 6),dtype=int)

i = 0
for pt in given_geo.points():
  cut_areas = list(map(float, pt.floatListAttribValue('cut_areas')))
  given = given.astype(float) #convert array given into float type
  given[i:] = cut_areas       #assign new values to given
  i+=1

for i in range(N):
  for j in range(N):
    rules[i][j][0] = (given[i][0] == given[j][2] )
    rules[i][j][1] = (given[i][1] == given[j][3] ) 
    rules[i][j][2] = (given[i][2] == given[j][0] )
    rules[i][j][3] = (given[i][3] == given[j][1] )
    rules[i][j][4] = (given[i][4] == given[j][5] )
    rules[i][j][5] = (given[i][5] == given[j][4] ) 
    #比如说010  (given[i][0] == given[j][2] ) 就是看0号点的左能不能和1号点的右相连, 前面rules[i][j][0] 因为有左右 右左 前后 后前 上下 下上 六种情况 所以rules有六种合集 比如010 就是0和1号点在左右这个方向上的适配情况011就是0和1号点在上下的情况

 
    for k in range(6):
      geo.point(i+j*N*6+k*N).setAttribValue('islegal', int(rules[i][j][k]))
      #geo.point(i*N*6+j*6+k).setAttribValue('islegal', rules[i][j][k])