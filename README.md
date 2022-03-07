# YingQ-for-Houdini
**1. 使用方法**  
  如果您的Houdini已经使用了json来管理, 直接在package_page中添加:"$LibPath/YingQ-for-Houdini/Packages"即可.  
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
**2. 所有的HDA/OTL都是commercial版本的, 目前为止所有HDA都是可以正常使用的, 不过其中有些完成度比较低, 欢迎如果有问题欢迎随时联系我!**  

## 相关  
 ContactMe: a1211094597@gmail.com
 Reference: ①https://github.com/Fe-Elf/FeELib-for-Houdini 
