#include "math.h"

// this file gathers operations that are not specific to one object



// this test function demonstrates how multiple outputs can be handled at once using export
vector testFunction(
		int myInt;
		float myFloat;
		vector2 myVector2;
		vector myVector3;
		vector4 myVector4;
		export int exInt; // exports mean that they will carry to computation outside
		export float exFloat;
		export vector2 exVector2;
		export vector exVector3;
		export vector4 exVector4;
		) {
	// just scale every single value by two
	exInt = 2*myInt;
	exFloat = 2*myFloat;
	exVector2 = 2*myVector2;
	exVector3 = 2*myVector3;
	exVector3 = 2*myVector3;
	exVector4 = 2*myVector4;
	
	// and return the vector3 as an example. You could also make this function void to not return anything
	return exVector3;
}




// return the constant gradient on the faces
// needs triangle geometry
void gradientField(
        export vector grad; // grad attrib holder
        string attribName; // attrib Name to compute gradient from
        int geo; // geo id with the attribute
        int primNum // id of face
        ){

    // collect corners
    int he = primhedge(geo,primNum);
    vector P1 = attrib(geo,'point','P',hedge_dstpoint(geo,he));
    int p1p=hedge_dstpoint(geo,he);
    he = hedge_next(geo,he);
    vector P2= attrib(geo,'point','P',hedge_dstpoint(geo,he));
    int p2p=hedge_dstpoint(geo,he);
    he = hedge_next(geo,he);
    vector P3= attrib(geo,'point','P',hedge_dstpoint(geo,he));
    int p3p=hedge_dstpoint(geo,he);
    
    // get edges and area
    vector v1=P3-P2;
    vector v2=P1-P3;
    vector v3=P2-P1;
    vector norm=cross(v1,v2)/length(cross(v1,v2));
    float area=length(cross(v1,v2));
    
    // compute gradient
    grad=1/(2*area)*(
             attrib(geo,'point',attribName,p1p)*v1
            +attrib(geo,'point',attribName,p2p)*v2
            +attrib(geo,'point',attribName,p3p)*v3);
    matrix3 m=ident();
    vector axis=norm;
    float angle=PI/2;
    rotate(m,angle,axis);
    grad*=m;
}


// averages the attribute of neighbour primitives to the point
// vector valued
void averagePrimToPoint(
        export vector attrib; // to store result
        int ptId; // id of point 
        string attributeName // attribute to call for
        ){
    int neighbourPrims[] = pointprims(0, ptId);
    vector vec = set(0,0,0);
    foreach (int i ; neighbourPrims){
        vec += attrib(0,"primitive",attributeName,i);
    }
    attrib = vec/len(neighbourPrims);
}


// orient an (0,1,0) pointing object to the
//new normal and rotate nicley
vector4 orientForCopy(
    vector normal; // final pointing direction
    float rotation // rotation on that final direction
    ){
    // default
    vector objectUp = set(0,1,0);

    // compute quaternions for rotation and orientation seperatly
    vector4 Qrotate = quaternion(rotation,objectUp);
    vector4 Qorient = dihedral(objectUp,normal);

    // collect
    return qmultiply(Qorient,Qrotate); // combine rotations

}
