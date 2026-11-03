"""Create an independent MuJoCo 3.4 teaching scene. Never alter official sources."""
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

HERE=Path(__file__).resolve().parent

def build_scene(*, gripper='official', target=(.40,.25), place=(.32,.10), camera_height=1.85, output=None):
    original=HERE/'official/xml/robot_grasper.xml'
    if not original.is_file():
        raise FileNotFoundError('Missing official/xml/robot_grasper.xml; restore the complete Course_Code model directory.')
    root=ET.parse(original).getroot()
    root.attrib['model']='TRON2 OpenCV tabletop teaching scene'
    root.find('compiler').attrib['meshdir']='../official/meshes'
    root.find('option').attrib.update(timestep='0.001',integrator='implicitfast',iterations='100',tolerance='1e-10',cone='elliptic')
    vis=ET.SubElement(root,'visual')
    ET.SubElement(vis,'global',offwidth='1280',offheight='960')
    ET.SubElement(vis,'quality',shadowsize='2048')
    wb=root.find('worldbody'); base=wb.find("body[@name='base_Link']")
    for j in list(base.findall('joint')): base.remove(j)
    # Fixing the root changes MuJoCo's automatic parent filtering for static bodies.
    # Explicitly preserve the adjacent base/shoulder exclusion, not object contacts.
    for side in 'LR':
        ET.SubElement(root.find('contact'),'exclude',body1='base_Link',body2=f'proximal_pitch_{side}_Link')
    for b in list(wb.findall('body')):
        if b.attrib.get('mocap')=='true': wb.remove(b)
    # A wrist-local site defines the actual palm/target frame; SDK transform is deliberately absent.
    for side in 'LR':
        gb=base.find(f".//body[@name='grasper_base_{side}_Link']")
        # Teaching TCP lies near distal finger pad centre; derived from mesh bounds.
        # It is a model-local definition, not a vendor-calibrated real TCP.
        ET.SubElement(gb,'site',name=f'tcp_{side}',pos='0 0 -0.165',size='.004',rgba='0 .4 1 .7')
    if gripper=='teaching_parallel':
        gb=base.find(".//body[@name='grasper_base_L_Link']")
        for child in list(gb.findall('body')):gb.remove(child)
        # Non-colliding massless visuals connect wrist, rail and moving finger pads.
        visual=dict(mass='0',contype='0',conaffinity='0',rgba='.28 .32 .40 1')
        ET.SubElement(gb,'geom',name='teaching_palm_stem_visual',type='cylinder',fromto='0 0 -.018 0 0 -.060',size='.018',**visual)
        ET.SubElement(gb,'geom',name='teaching_slide_rail_visual',type='box',pos='0 0 -.065',size='.025 .058 .009',**visual)
        # This is an openly simplified two-slide teaching gripper, NOT the vendor gripper.
        for side,y,axis in [('left',.05,'0 -1 0'),('right',-.05,'0 1 0')]:
            b=ET.SubElement(gb,'body',name=f'teaching_finger_{side}',pos=f'0 {y} -.165')
            ET.SubElement(b,'joint',name=f'teaching_finger_{side}_joint',type='slide',axis=axis,range='0 .045',damping='1',armature='.002')
            ET.SubElement(b,'geom',name=f'teaching_pad_{side}',type='box',size='.022 .006 .025',mass='.05',rgba='.15 .25 .45 1',friction='1.2 .01 .001',condim='4',solref='.004 1')
            ET.SubElement(b,'geom',name=f'teaching_finger_rod_{side}_visual',type='box',pos='0 0 .050',size='.012 .006 .050',**visual)
        for group in ('sensor','actuator','equality','contact'):
            node=root.find(group)
            for element in list(node):
                values=' '.join(element.attrib.values())
                if 'grasper_L_' in values:node.remove(element)
        for side in ('left','right'):
            ET.SubElement(root.find('actuator'),'motor',name=f'teaching_finger_{side}',joint=f'teaching_finger_{side}_joint',ctrlrange='-8 8',gear='1')
    # Table, free object, and fiducials. RGB image is the sole input to detection.
    table_z=.68
    ET.SubElement(wb,'geom',name='table',type='box',pos=f'.48 0 {table_z-.025}',size='.36 .42 .025',rgba='.65 .62 .58 1',friction='.9 .01 .001',condim='4',margin='0')
    for x in (.20,.76):
        for y in (-.34,.34):
            ET.SubElement(wb,'geom',name=f'marker_{x}_{y}',type='cylinder',pos=f'{x} {y} {table_z+.001}',size='.013 .001',rgba='0 .9 .1 1',contype='0',conaffinity='0')
    for idx,(x,y) in enumerate(((.30,-.23),(.48,-.23),(.66,-.23),(.30,.1),(.66,.25))):
        ET.SubElement(wb,'geom',name=f'check_{idx}',type='cylinder',pos=f'{x} {y} {table_z+.001}',size='.008 .001',rgba='.02 .12 .9 1',contype='0',conaffinity='0')
    for x in (.18,.78):
        for y in (-.36,.36):
            ET.SubElement(wb,'geom',type='box',pos=f'{x} {y} .3275',size='.018 .018 .3275',rgba='.2 .21 .23 1')
    obj=ET.SubElement(wb,'body',name='object',pos=f'{target[0]} {target[1]} {table_z+.020}',quat='.4975710479 0 0 .8674232256')
    ET.SubElement(obj,'freejoint',name='object_free')
    ET.SubElement(obj,'geom',name='object_geom',type='box',size='.018 .018 .020',mass='.05',rgba='.9 .015 .01 1',friction='1.2 .02 .001',condim='4',solref='.004 1',margin='0')
    ET.SubElement(wb,'geom',name='place_marker',type='cylinder',pos=f'{place[0]} {place[1]} {table_z+.0005}',size='.035 .0005',rgba='.65 .15 .75 .45',contype='0',conaffinity='0')
    # A truly fixed, top-down pinhole camera: camera local -Z points at table.
    ET.SubElement(wb,'camera',name='top',pos=f'.48 0 {camera_height}',xyaxes='1 0 0 0 1 0',fovy='48')
    ET.SubElement(wb,'camera',name='overview',pos='1.95 -1.9 1.75',xyaxes='.707 .707 0 -.19 .19 .963',fovy='42')
    eye=np.array([.90,-.40,1.15]);look=np.array([.38,.18,.76]);z=(eye-look)/np.linalg.norm(eye-look)
    x=np.cross([0.,0.,1.],z);x=x/np.linalg.norm(x);y=np.cross(z,x)
    ET.SubElement(wb,'camera',name='detail',pos=' '.join(map(str,eye)),xyaxes=' '.join(map(str,np.r_[x,y])),fovy='35')
    out=output or HERE/'scenes'/f'tabletop_{gripper}.xml'
    out.parent.mkdir(parents=True,exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(out,encoding='utf-8',xml_declaration=True)
    return out

if __name__=='__main__': print(build_scene())
