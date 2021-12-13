/*
 * Copyright (c) 2021
 *	Side Effects Software Inc.  All rights reserved.
 *
 * Redistribution and use of Houdini Development Kit samples in source and
 * binary forms, with or without modification, are permitted provided that the
 * following conditions are met:
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. The name of Side Effects Software may not be used to endorse or
 *    promote products derived from this software without specific prior
 *    written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY SIDE EFFECTS SOFTWARE `AS IS' AND ANY EXPRESS
 * OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 * OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN
 * NO EVENT SHALL SIDE EFFECTS SOFTWARE BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
 * OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
 * LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
 * EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 *----------------------------------------------------------------------------
 * The Star SOP
 */

#include "SOP_Star.h"

// This is an automatically generated header file based on theDsFile, below,
// to provide SOP_StarParms, an easy way to access parameter values from
// SOP_StarVerb::cook with the correct type.
#include "SOP_Star.proto.h"

#include <GU/GU_Detail.h>
#include <GEO/GEO_PrimPoly.h>
#include <OP/OP_Operator.h>
#include <OP/OP_OperatorTable.h>
#include <PRM/PRM_Include.h>
#include <PRM/PRM_TemplateBuilder.h>
#include <UT/UT_DSOVersion.h>
#include <UT/UT_Interrupt.h>
#include <UT/UT_StringHolder.h>
#include <SYS/SYS_Math.h>
#include <limits.h>

using namespace HDK_Sample;

//
// Help is stored in a "wiki" style text file.  This text file should be copied
// to $HOUDINI_PATH/help/nodes/sop/star.txt
//
// See the sample_install.sh file for an example.
//

/// This is the internal name of the SOP type.
/// It isn't allowed to be the same as any other SOP's type name.
const UT_StringHolder SOP_Star::theSOPTypeName("hdk_star"_sh);

/// newSopOperator is the hook that Houdini grabs from this dll
/// and invokes to register the SOP.  In this case, we add ourselves
/// to the specified operator table.
void
newSopOperator(OP_OperatorTable *table)
{
    table->addOperator(new OP_Operator(
        SOP_Star::theSOPTypeName,   // Internal name
        "Star",                     // UI name
        SOP_Star::myConstructor,    // How to build the SOP
        SOP_Star::buildTemplates(), // My parameters
        0,                          // Min # of sources
        0,                          // Max # of sources
        nullptr,                    // Custom local variables (none)
        OP_FLAG_GENERATOR));        // Flag it as generator
}

/// This is a multi-line raw string specifying the parameter interface
/// for this SOP.
static const char *theDsFile = R"THEDSFILE(
{
    name        parameters
    parm {
        name    "divs"      // Internal parameter name
        label   "Divisions" // Descriptive parameter name for user interface
        type    integer
        default { "5" }     // Default for this parameter on new nodes
        range   { 2! 50 }   // The value is prevented from going below 2 at all.
                            // The UI slider goes up to 50, but the value can go higher.
        export  all         // This makes the parameter show up in the toolbox
                            // above the viewport when it's in the node's state.
    }
    parm {
        name    "rad"
        label   "Radius"
        type    vector2
        size    2           // 2 components in a vector2
        default { "1" "0.3" } // Outside and inside radius defaults
    }
    parm {
        name    "nradius"
        label   "Allow Negative Radius"
        type    toggle
        default { "0" }
    }
    parm {
        name    "t"
        label   "Center"
        type    vector
        size    3           // 3 components in a vector
        default { "0" "0" "0" }
    }
    parm {
        name    "orient"
        label   "Orientation"
        type    ordinal
        default { "0" }     // Default to first entry in menu, "xy"
        menu    {
            "xy"    "XY Plane"
            "yz"    "YZ Plane"
            "zx"    "ZX Plane"
        }
    }
}
)THEDSFILE";

PRM_Template*
SOP_Star::buildTemplates()
{
    static PRM_TemplateBuilder templ("SOP_Star.C"_sh, theDsFile);
    return templ.templates();
}

class SOP_StarVerb : public SOP_NodeVerb
{
public:
    SOP_StarVerb() {}
    virtual ~SOP_StarVerb() {}

    virtual SOP_NodeParms *allocParms() const { return new SOP_StarParms(); }
    virtual UT_StringHolder name() const { return SOP_Star::theSOPTypeName; }

    virtual CookMode cookMode(const SOP_NodeParms *parms) const { return COOK_GENERIC; }

    virtual void cook(const CookParms &cookparms) const;
    
    /// This static data member automatically registers
    /// this verb class at library load time.
    static const SOP_NodeVerb::Register<SOP_StarVerb> theVerb;
};

// The static member variable definition has to be outside the class definition.
// The declaration is inside the class.
const SOP_NodeVerb::Register<SOP_StarVerb> SOP_StarVerb::theVerb;

const SOP_NodeVerb *
SOP_Star::cookVerb() const 
{ 
    return SOP_StarVerb::theVerb.get();
}

/// This is the function that does the actual work.
void
SOP_StarVerb::cook(const SOP_NodeVerb::CookParms &cookparms) const
{
    auto &&sopparms = cookparms.parms<SOP_StarParms>();
    GU_Detail *detail = cookparms.gdh().gdpNC();

    // We need two points per division
    exint npoints = sopparms.getDivs()*2;

    if (npoints < 4)
    {
        // With the range restriction we have on the divisions, this
        // is actually impossible, (except via integer overflow),
        // but it shows how to add an error message or warning to the SOP.
        cookparms.sopAddWarning(SOP_MESSAGE, "There must be at least 2 divisions; defaulting to 2.");
        npoints = 4;
    }

    // If this SOP has cooked before and it wasn't evicted from the cache,
    // its output detail will contain the geometry from the last cook.
    // If it hasn't cooked, or if it was evicted from the cache,
    // the output detail will be empty.
    // This knowledge can save us some effort, e.g. if the number of points on
    // this cook is the same as on the last cook, we can just move the points,
    // (i.e. modifying P), which can also save some effort for the viewport.

    GA_Offset start_ptoff;
    if (detail->getNumPoints() != npoints)
    {
        // Either the SOP hasn't cooked, the detail was evicted from
        // the cache, or the number of points changed since the last cook.

        // This destroys everything except the empty P and topology attributes.
        detail->clearAndDestroy();

        // Build 1 closed polygon (as opposed to a curve),
        // namely that has its closed flag set to true,
        // and the right number of vertices, as a contiguous block
        // of vertex offsets.
        GA_Offset start_vtxoff;
        detail->appendPrimitivesAndVertices(GA_PRIMPOLY, 1, npoints, start_vtxoff, true);

        // Create the right number of points, as a contiguous block
        // of point offsets.
        start_ptoff = detail->appendPointBlock(npoints);

        // Wire the vertices to the points.
        for (exint i = 0; i < npoints; ++i)
        {
            detail->getTopology().wireVertexPoint(start_vtxoff+i,start_ptoff+i);
        }

        // We added points, vertices, and primitives,
        // so this will bump all topology attribute data IDs,
        // P's data ID, and the primitive list data ID.
        detail->bumpDataIdsForAddOrRemove(true, true, true);
    }
    else
    {
        // Same number of points as last cook, and we know that last time,
        // we created a contiguous block of point offsets, so just get the
        // first one.
        start_ptoff = detail->pointOffset(GA_Index(0));

        // We'll only be modifying P, so we only need to bump P's data ID.
        detail->getP()->bumpDataId();
    }

    // Everything after this is just to figure out what to write to P and write it.

    const SOP_StarParms::Orient plane = sopparms.getOrient();
    const bool allow_negative_radius = sopparms.getNradius();

    UT_Vector3 center = sopparms.getT();

    int xcoord, ycoord, zcoord;
    switch (plane)
    {
        case SOP_StarParms::Orient::XY:         // XY Plane
            xcoord = 0;
            ycoord = 1;
            zcoord = 2;
            break;
        case SOP_StarParms::Orient::YZ:         // YZ Plane
            xcoord = 1;
            ycoord = 2;
            zcoord = 0;
            break;
        case SOP_StarParms::Orient::ZX:         // XZ Plane
            xcoord = 0;
            ycoord = 2;
            zcoord = 1;
            break;
    }

    // Start the interrupt scope
    UT_AutoInterrupt boss("Building Star");
    if (boss.wasInterrupted())
        return;

    float tinc = M_PI*2 / (float)npoints;
    float outer_radius = sopparms.getRad().x();
    float inner_radius = sopparms.getRad().y();
    
    // Now, set all the points of the polygon
    for (exint i = 0; i < npoints; i++)
    {
        // Check to see if the user has interrupted us...
        if (boss.wasInterrupted())
            break;

        float angle = (float)i * tinc;
        bool odd = (i & 1);
        float rad = odd ? inner_radius : outer_radius;
        if (!allow_negative_radius && rad < 0)
            rad = 0;

        UT_Vector3 pos(SYScos(angle)*rad, SYSsin(angle)*rad, 0);
        // Put the circle in the correct plane.
        pos = UT_Vector3(pos(xcoord), pos(ycoord), pos(zcoord));
        // Move the circle to be centred at the correct position.
        pos += center;

        // Since we created a contiguous block of point offsets,
        // we can just add i to start_ptoff to find this point offset.
        GA_Offset ptoff = start_ptoff + i;
        detail->setPos3(ptoff, pos);
    }
}
