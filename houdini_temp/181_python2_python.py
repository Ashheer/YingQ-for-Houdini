node = hou.pwd()
geo = node.geometry()

import numpy as np
import potpourri3d as pp3d

P = np.array([geo.pointFloatAttribValues("px"), geo.pointFloatAttribValues("py"), geo.pointFloatAttribValues("pz")])
P = np.stack((P), axis = 1)

# = Stateful solves
#P = # a Nx3 numpy array of points
solver = pp3d.PointCloudHeatSolver(P)

# Compute the geodesic distance to point 4
dists = solver.compute_distance(4)

geo.setPointFloatAttribValuesFromString("dist", dists.astype(np.float32))

# Extend the value `0.` from point 12 and `1.` from point 17. Any point 
# geodesically closer to 12. will take the value 0., and vice versa 
# (plus some slight smoothing)
ext = solver.extend_scalar([12, 17, 33], [0.0, 0.5, 1.0])
geo.setPointFloatAttribValuesFromString("ext", ext.astype(np.float32))

# Get the tangent frames which are used by the solver to define tangent data
# at each point
basisX, basisY, basisN = solver.get_tangent_frames()

# Parallel transport a vector along the surface
# (and map it to a vector in 3D)
sourceP = 0
ext = solver.transport_tangent_vector(sourceP, [1., 0.])
ext3D = ext[:,0,np.newaxis] * basisX +  ext[:,1,np.newaxis] * basisY

ext3dx = ext3D[:,0]
ext3dy = ext3D[:,1]
ext3dz = ext3D[:,2]

geo.setPointFloatAttribValuesFromString("ext3dx", ext3dx.astype(np.float32))
geo.setPointFloatAttribValuesFromString("ext3dy", ext3dy.astype(np.float32))
geo.setPointFloatAttribValuesFromString("ext3dz", ext3dz.astype(np.float32))

# Compute the logarithmic map
logmap = solver.compute_log_map(sourceP)
logmapu = logmap[:,0]
logmapv = logmap[:,1]
geo.setPointFloatAttribValuesFromString("logmapu", logmapu.astype(np.float32))
geo.setPointFloatAttribValuesFromString("logmapv", logmapv.astype(np.float32))