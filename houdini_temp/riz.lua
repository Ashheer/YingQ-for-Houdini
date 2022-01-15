
ZomLoad({File={Path="C:/Users/YingQ/sidefx_rizom_bridge_untitled_rizomuv_unwrap1.fbx", ImportGroups=true, XYZ=true}, NormalizeUVW=true})

ZomSelect({PrimType="Edge", Select=true, ResetBefore=true, ProtectMapName="Protect", FilterIslandVisible=true, Auto={Skeleton={Open=true}, PipesCutter=false, HandleCutter=true}})
ZomCut({PrimType="Edge"})
ZomUnfold({PrimType="Edge", MinAngle=1e-005, Mix=1, Iterations=1, PreIterations=5, StopIfOutOFDomain=false, RoomSpace=0, PinMapName="Pin", ProcessNonFlats=true, ProcessSelection=true, ProcessAllIfNoneSelected=true, ProcessJustCut=true, BorderIntersections=true, TriangleFlips=true})
ZomIslandGroups({Mode="DistributeInTilesEvenly", MergingPolicy=8322, GroupPath="RootGroup"})ZomPack({ProcessTileSelection=false, RecursionDepth=1, RootGroup="RootGroup", Scaling={Mode=2}, Rotate={}, Translate=true, LayoutScalingMode=2})

ZomSave({File={Path="C:/Users/YingQ/sidefx_rizom_bridge_untitled_rizomuv_unwrap1.fbx", UVWProps=true}, __UpdateUIObjFileName=true})
ZomQuit()
