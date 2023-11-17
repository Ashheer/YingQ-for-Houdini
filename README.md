**使用方法**  
  如果您的Houdini已经使用了package来管理, 直接在.json里面package_path中添加:"$LibPath/YingQ-for-Houdini/Packages"即可.  
  反之则在C:\Users\Username\Documents\houdiniVersion\packages中创建一个.json,  
{  
"env": [  
{  
"LibPath":"~"  
},  
],  
"package_path" : [  
"$LibPath/YingQ-for-Houdini/Packages.",  
],  
}  
LibPath里面填入自己想要放的路径, 然后就可以找到YingQ-for-Houdini/Packages里面的package文件了, 也就是YingQ_lib.json! Houdini就可以识别lib里面的所有内容了!  
