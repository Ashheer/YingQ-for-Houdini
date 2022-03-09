# Automatically generated script
\set noalias = 1
if ( "$argc" != 2 ) then
   echo -n Usage: $arg0  op_name1
   exit
endif
set saved_path = `execute("oppwf")`
opcf /obj/geo1

# Node $arg1 (Sop/subnet)
set arg1 = `run("opadd -n -u -e -v testmenu_ying")`
oplocate -x -2.9632352941176472 -y 0.1992647058823529 $arg1
opspareds "" $arg1
opparm -V 19.0.531 $arg1 label1 ( 'Sub-Network Input #1' ) label2 ( 'Sub-Network Input #2' ) label3 ( 'Sub-Network Input #3' ) label4 ( 'Sub-Network Input #4' )
chlock $arg1 -*
chautoscope $arg1 -*
opset -d on -r on -h off -f off -y off -t off -l off -s off -u off -F on -c on -e on -b off $arg1
opexprlanguage -s hscript $arg1

opcf /obj/geo1
opcf $saved_path
