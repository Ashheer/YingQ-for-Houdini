from collections import Counter
from itertools import chain
import numpy as np
from builtins import range

# Geo I/O
node = hou.pwd()
inputs = node.inputs()
geo = node.geometry()
geo.addAttrib(hou.attribType.Point, "rotation", 0)
geo.addAttrib(hou.attribType.Point, "scaleX", 1)
OutputPoints = geo.points()
HDANode = node.parent()


# User Parms
Seed = HDANode.parm("iSeed").evalAsInt()
N = HDANode.parm("iPatternSearchSize").evalAsInt()
MaxNumberContradictionTries = HDANode.parm("iNumSolveAttempts").evalAsInt()
UseInputPatternFrequency = HDANode.parm("bObservedPatternFreq").evalAsInt()
TileAroundBounds = True if HDANode.parm("bTileableOutput").evalAsInt() == 1 else False
PeriodicInput = True if HDANode.parm("bPeriodicInputPatterns").evalAsInt() == 1 else False
RespectUserConstraints = True if HDANode.parm("bUserConstraints").evalAsInt() == 1 else False
AddRotations = True if HDANode.parm("bGenerateRotations").evalAsInt() == 1 else False
InputGridSize = (inputs[2].geometry().attribValue("SampleGridResolutionX"), inputs[2].geometry().attribValue("SampleGridResolutionZ"))
if HDANode.parm("bAutomaticInputSizeDetection").evalAsInt() == 0:
    InputGridSize = HDANode.parmTuple("i2SampleGridSize").eval()

OutputGridSize = (inputs[2].geometry().attribValue("OutputGridResolutionX"), inputs[2].geometry().attribValue("OutputGridResolutionZ"))
if HDANode.parm("bAutomaticOutputSizeDetection").evalAsInt() == 0:
    OutputGridSize = HDANode.parmTuple("i2OutputGridSize").eval()

# print "Sample", InputGridSize
#print "Output", OutputGridSize

SolveStartingPointIndex = None
if HDANode.parm("bStartingPoint").evalAsInt() == 1:
    SolveStartingPointIndex = HDANode.parm("iStartingPoint").evalAsInt()


# Internal Parms
PatternFrequencies = None
Patterns = None
PatternsTransforms = None #["rot", "flipX", "flipY"]
NumberOfUniquePatterns = None
NbrDirections = ((-1, 0), (1, 0), (0, -1), (0, 1))
OutputGrid = {}
EntropyGrid = {}
AllowedPatternAdjacencies = {}
InputSampleAttributes = []
UserConstraintAttributes = []



def ReadInputAttributes(points):
    AttributeList = []

    for point in points:
        AttributeList.append(point.attribValue("name"))
    return AttributeList


# Here we will take the input grid, extract its values (name attribute), and cut it up into NxN size patterns
def CreatePatternsFromInput():
    global PatternFrequencies, Patterns, PatternsTransforms, NumberOfUniquePatterns

    # Kernel to store data in
    SearchKernel = tuple(tuple(i + n*InputGridSize[0] for i in range(N)) for n in range(N))

    AllTempPatterns = []
    AllTempPatternsTransforms = []

    if PeriodicInput:
        Offset = 0
    else:
        Offset = (N-1)

    # Loop over grid
    for y in range(InputGridSize[1] - Offset):
        for x in range(InputGridSize[0] - Offset):
 
            MatrixAsList = []
            for item in SearchKernel:
                Tmp = []
                for subitems in item:
                    listindex = ((x+subitems) %InputGridSize[0]) + ((( item[0] + InputGridSize[0] * y) // InputGridSize[0]) % InputGridSize[1]) * InputGridSize[0]
                    Tmp.append(InputSampleAttributes[listindex])
                    
                # This is where variations would take place

                MatrixAsList.append(tuple(Tmp))

            TempPattern = tuple(MatrixAsList)

            if not AddRotations:
                AllTempPatterns.append(TempPattern)
                AllTempPatternsTransforms.append([0, 1, 1])
            else:
                for x in range(4):
                    TempPattern = list(zip(*TempPattern[::-1]))
                    AllTempPatterns.append(TempPattern)
                    AllTempPatternsTransforms.append([(x+1)*90, 1, 1])
                    # Maybe add horizontal flip too??
                    # AllTempPatterns.append([a[::-1] for a in TempPattern]) # Flip X
                    # AllTempPatternsTransforms.append([(x+1)*90, -1, 1])


    AllTempPatterns = [tuple(chain.from_iterable(p)) for p in AllTempPatterns]

    Patterns = []
    PatternsTransforms = []
    PatternFrequencies = []

    for i, pattern in enumerate(AllTempPatterns):
        if pattern not in Patterns:
            Patterns.append(pattern)
            PatternFrequencies.append(1)
            PatternsTransforms.append(AllTempPatternsTransforms[i])
        else:
            index = Patterns.index(pattern)
            PatternFrequencies[index] += 1

    NumberOfUniquePatterns = len(PatternFrequencies)
    

# Here we create a list that will be used as our output grid. (Used for solving in)
def InitializeGrid():
    global OutputGrid

    for x in range(OutputGridSize[0]*OutputGridSize[1]):
        OutputGrid[x] = list(range(NumberOfUniquePatterns))


# Here we create grid that matches the output grid, but we store entropy values instead. (Entropy = Number of remaining legal patterns)
def InitializeEntropyGrid():
    global EntropyGrid, SolveStartingPointIndex

    for x in range(OutputGridSize[0]*OutputGridSize[1]):
        EntropyGrid[x] = NumberOfUniquePatterns

    # Pick starting point for solve. (Random if not specified)
    if SolveStartingPointIndex == None:
        #SolveStartingPointIndex = np.random.randint(NumberOfUniquePatterns)
        #### LOOK HERE
        SolveStartingPointIndex = np.random.randint(len(EntropyGrid.keys()))

    EntropyGrid[SolveStartingPointIndex] = NumberOfUniquePatterns-1


def CalculateAdjacencies():
    global AllowedPatternAdjacencies

    # If PatternIndex = 10 has been observed to be to the left of of PatternIndex = 15 in the InputGrid:
    # AllowedPatternAdjacencies[PatternIndex=15][0].add(PatternIndex=10)
    # Directions: 0 = left, 1 = right, 2 = up, 3 = down
    # OUTPUT EXAMPLE PatternIndex = 15 --> (LEFT: set([65, 36, 69, 44, 87, 56, 29]), RIGHT: set([8]), UP: set([32, 64, 10, 12, 14, 83, 21]), DOWN: set([15]))


    # Initialize empty AllowedPatternAdjacencies
    for x in range(NumberOfUniquePatterns):
        AllowedPatternAdjacencies[x] = tuple(list() for direction in range(len(NbrDirections)))

    # Comparing patterns to each other
    for PatternIndex1 in range(NumberOfUniquePatterns):
        for PatternIndex2 in range(NumberOfUniquePatterns):

            Pattern1BoundaryColumns = [n for i, n in enumerate(Patterns[PatternIndex1]) if i%N!=(N-1)]
            Pattern2BoundaryColumns = [n for i, n in enumerate(Patterns[PatternIndex2]) if i%N!=0]

            # Compare Columns compatability
            if Pattern1BoundaryColumns == Pattern2BoundaryColumns:
                AllowedPatternAdjacencies[PatternIndex1][0].append(PatternIndex2)
                AllowedPatternAdjacencies[PatternIndex2][1].append(PatternIndex1)

            Pattern1BoundaryRows = Patterns[PatternIndex1][:(N*N)-N]
            Pattern2BoundaryRows = Patterns[PatternIndex2][N:]

            if Pattern1BoundaryRows == Pattern2BoundaryRows:
                AllowedPatternAdjacencies[PatternIndex1][2].append(PatternIndex2)
                AllowedPatternAdjacencies[PatternIndex2][3].append(PatternIndex1)


# Find the list entry in the entropy grid with the lowest value
def GetLowestEntropyCell():
    return min(EntropyGrid, key = EntropyGrid.get)


# Assign a random allowed PatternIndex to given cell. This can either use frequency of found patterns as a weighted random or not depending on user parm
def GetRandomAllowedPatternIndexFromCell(cell):
    if UseInputPatternFrequency == 1:
        return np.random.choice([PatternIndex for PatternIndex in OutputGrid[cell] for i in range(PatternFrequencies[PatternIndex])])
    else:
        return np.random.choice([PatternIndex for PatternIndex in OutputGrid[cell]])


# Assign given cell a chosen PatternIndex, and delete cell from EntropyGrid (Cell has collapsed)
def AssignPatternToCell(cell, PatternIndex):
    global OutputGrid, EntropyGrid
    OutputGrid[cell] = {PatternIndex}
    del EntropyGrid[cell]


# This propagates all the cells that should have been affected from the just-collapsed cell
def PropagateGridCells(cell):
    global EntropyGrid, OutputGrid

    # We are using a stack to add newly found to-be-updated cells to
    ToUpdateStack = {cell}
    while len(ToUpdateStack) != 0:
        CellIndex = ToUpdateStack.pop() 

        # loop through neighbor cells of currently propagated cell
        for direction, transform in enumerate(NbrDirections):
            
            NeighborIndexIsValid = True

            x = (CellIndex%OutputGridSize[0] + transform[0])%OutputGridSize[0]
            y = (CellIndex//OutputGridSize[0] + transform[1])%OutputGridSize[1]
            NeighborCellIndex = x + y * OutputGridSize[0] # index of negihboring cell


            # If the user does not want the WFC solve to create a tiling output, we just state that the found neighbor cell is invalid and don't propagate it
            if not TileAroundBounds:
                xiswrapping = abs(NeighborCellIndex % OutputGridSize[0] - CellIndex % OutputGridSize[0]) > 1
                yiswrapping = abs(NeighborCellIndex // OutputGridSize[1] - CellIndex // OutputGridSize[1]) > 1

                if xiswrapping or yiswrapping:
                    NeighborIndexIsValid = False
                

            # Cell has not yet been collapsed yet
            if NeighborCellIndex in EntropyGrid and NeighborIndexIsValid:   

                # These are all the allowed patterns for the direction of the checked neighbor cell
                PatternIndicesInCell = {n for PatternIndex in OutputGrid[CellIndex] for n in AllowedPatternAdjacencies[PatternIndex][direction]}

                # These are all the patterns the neighbor allows itself
                PatternIndicesInNeighborCell = OutputGrid[NeighborCellIndex]

                # Make sure we need to update the cell by checking if the currently PatternIndicesInCell patterns for the neighbor cells are already fully contained
                # in the now reduced set of patterns
                if not set(PatternIndicesInNeighborCell).issubset(set(PatternIndicesInCell)):

                    SharedCellAndNeighborPatternIndices = [x for x in PatternIndicesInCell if x in PatternIndicesInNeighborCell]

                    if len(SharedCellAndNeighborPatternIndices) == 0:
                        return False, 1

                    SharedCellAndNeighborPatternIndices.sort()
                    OutputGrid[NeighborCellIndex] = SharedCellAndNeighborPatternIndices
                    EntropyGrid[NeighborCellIndex] = len(OutputGrid[NeighborCellIndex])
                    ToUpdateStack.add(NeighborCellIndex)  

    return True, 0


# This is a utility function that chops a given list into lists of size N 
def CutListInChunksOfSize(l, n):
    n = max(1, n)
    return (l[i:i+n] for i in range(0, len(l), n)) 


# This is a utility function that prints the entropy grid in a userfriendly format
def PrintEntropyGridStatus():
    PrintEntropyGrid = []
    for i in range(OutputGridSize[0] * OutputGridSize[1]):
        Value = 1
        if EntropyGrid.has_key(i):
            Value = EntropyGrid[i]
        PrintEntropyGrid.append(Value)

    print (list(CutListInChunksOfSize(PrintEntropyGrid, OutputGridSize[0])))


# This finds and assigns the picked PatternIndex to the output grid as attributes
def AssignWaveToOutputGrid():
    for x in OutputGrid.keys():
        val = next(iter(OutputGrid[x])) 
        OutputPoints[x].setAttribValue("name", Patterns[val][0])
        OutputPoints[x].setAttribValue("rotation", PatternsTransforms[val][0])
        OutputPoints[x].setAttribValue("scaleX", PatternsTransforms[val][1])



# This function handles assigning user constraints based on attached attribute values to the output grid
def ForceUserConstraints():
    for cellindex, cellvalue in enumerate(UserConstraintAttributes):
        if cellvalue != "WFC_Initialize" and cellvalue in UserConstraintAttributes:
            AllowedIndices = [x for x in range(len(Patterns)) if cellvalue == Patterns[x][0]]

            PickedIndex = np.random.choice([PatternIndex for PatternIndex in AllowedIndices])
            AssignPatternToCell(cellindex, PickedIndex)
            Running, Error = PropagateGridCells(cellindex)
            if Error == 1:
                return False
    return True


# This runs the actual WFC solve
def RunWFCSolve(): 
    Running = True
    while Running:

        # Find the cell with the lowest entropy value, and assign a random valid PatternIndex
        LowestEntropyCell = GetLowestEntropyCell()

        PatternIndexForCell = GetRandomAllowedPatternIndexFromCell(LowestEntropyCell)
        AssignPatternToCell(LowestEntropyCell, PatternIndexForCell)

        # Propagate the OutputGrid after collapsing the LowestEntropyCell
        Running, Error = PropagateGridCells(LowestEntropyCell)

        # Check if all cells in the OutputGrid have collapsed yet (done solving)
        if len(EntropyGrid.keys()) == 0:
            Running = False

    # Check if we have ran into an error. (contradiction while propagating)
    if Error == 1:
        return False
    else:    
        return True


#### START THE WFC ALGORITHM SOLVE BELOW
##################################
InputSampleAttributes = ReadInputAttributes(inputs[1].geometry().points())
UserConstraintAttributes = ReadInputAttributes(OutputPoints)


with hou.InterruptableOperation("Solving WFC", open_interrupt_dialog=True) as Operation:

    # try:
    # # The WFC solve has been wrapped in a loop that tries solving at different seed values in case a contradiction has been found
    for solveattempt in range(MaxNumberContradictionTries): 

        # Set the seed for our random picking of values
        np.random.seed(Seed+solveattempt*100)
        Operation.updateProgress(float(solveattempt) / float(MaxNumberContradictionTries))

        # Initialize WFC process
        CreatePatternsFromInput()
        InitializeGrid()
        InitializeEntropyGrid()
        CalculateAdjacencies()
        
        if RespectUserConstraints:
            Success = ForceUserConstraints()
        else: Success = True

        if Success:
            # Run the WFC solve itself
            Success = RunWFCSolve()

        # Assign the outputvalues
        if Success == True:
            AssignWaveToOutputGrid()
            break

        # If we have exceeded the number of retries for the solve, we will throw an error to tell the user no solution has been found
        if solveattempt == MaxNumberContradictionTries-1 and Success == False:
            print ("Surpassed max number of contradiction retries.... Aborting")


# TODO:
# 1. Allow certain pieces to only instantiate once