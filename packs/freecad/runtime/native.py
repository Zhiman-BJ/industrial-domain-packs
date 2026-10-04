"""Fixed FreeCAD operations. No user Python, macro, command or expression evaluation."""
import hashlib
import json
import math
import os
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET

import FreeCAD as App
import Part
import Sketcher
import Mesh

ROOT = os.path.dirname(os.path.abspath(__file__))
request = json.load(open(os.path.join(ROOT, 'request.json'), encoding='utf-8'))

def write(name, value):
    with open(os.path.join(ROOT, name), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, allow_nan=False, indent=2)

def metrics(shape):
    b = shape.BoundBox
    return dict(valid=not shape.isNull() and shape.isValid(), volume=shape.Volume,
                solids=len(shape.Solids), faces=len(shape.Faces), bounds=[b.XLength, b.YLength, b.ZLength])

def safe_document(file):
    if file.lower().endswith('.fcstd'):
        with zipfile.ZipFile(file) as archive:
            if len(archive.infolist()) > 2000 or sum(i.file_size for i in archive.infolist()) > 64*1024*1024:
                raise ValueError('FCStd decompressed size limit exceeded')
            xml = archive.read('Document.xml')
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                raise ValueError('FCStd XML entities are unsupported')
            root = ET.fromstring(xml)
            allowed = {'Part::Feature', 'Part::Box', 'Part::Cylinder', 'Part::Cut', 'Part::Fuse', 'Part::Common', 'PartDesign::Body', 'PartDesign::Pad', 'Sketcher::SketchObject', 'App::DocumentObjectGroup', 'App::Origin', 'App::Line', 'App::Plane', 'App::Point'}
            objects = root.findall('./Objects/Object')
            if not objects or len(objects) > 200 or any(o.get('type') not in allowed for o in objects):
                raise ValueError('FCStd contains unsupported features or Python proxies')
            if root.findall('.//Expression') or any('Python' in str(e.attrib) or 'Python' in e.tag for e in root.iter()):
                raise ValueError('FCStd expressions and Python proxies are unsupported')
        doc = App.openDocument(file)
        candidates = [o for o in doc.Objects if hasattr(o, 'Shape') and not o.Shape.isNull() and o.Shape.Solids]
        result = doc.getObject('HarnessResult') or (candidates[-1] if candidates else None)
        if not result:
            raise ValueError('FCStd has no supported solid')
        return doc, result
    doc = App.newDocument('Imported')
    shape = Part.Shape()
    shape.read(file)
    result = doc.addObject('Part::Feature', 'HarnessResult')
    result.Shape = shape
    doc.recompute()
    return doc, result

def build(recipe):
    doc = App.newDocument('HarnessCAD')
    params = doc.addObject('App::DocumentObjectGroup', 'Parameters')
    for name, value in recipe['parameters'].items():
        params.addProperty('App::PropertyFloat', name, 'Dimensions')
        setattr(params, name, value)
    features = {}
    sketches = []
    for item in recipe['features']:
        op, name = item['op'], item['id']
        p = App.Vector(*item.get('origin', [0, 0, 0]))
        if op == 'box':
            obj = doc.addObject('Part::Box', name)
            obj.Length, obj.Width, obj.Height = item['length'], item['width'], item['height']
            obj.Placement.Base = p
        elif op == 'cylinder':
            obj = doc.addObject('Part::Cylinder', name)
            obj.Radius, obj.Height = item['radius'], item['height']
            obj.Placement.Base = p
        elif op == 'sketch_pad':
            body = doc.addObject('PartDesign::Body', name + 'Body')
            body.Placement.Base = p
            sketch = doc.addObject('Sketcher::SketchObject', name + 'Sketch')
            body.addObject(sketch)
            if item['profile'] == 'rectangle':
                l, w = item['length'], item['width']
                points = [App.Vector(0,0,0), App.Vector(l,0,0), App.Vector(l,w,0), App.Vector(0,w,0)]
                for i in range(4):
                    sketch.addGeometry(Part.LineSegment(points[i], points[(i+1)%4]), False)
                for i in range(4):
                    sketch.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i+1)%4, 1))
                for i in [0,2]: sketch.addConstraint(Sketcher.Constraint('Horizontal', i))
                for i in [1,3]: sketch.addConstraint(Sketcher.Constraint('Vertical', i))
                sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 1, -1, 1))
                sketch.addConstraint(Sketcher.Constraint('Distance', 0, l))
                sketch.addConstraint(Sketcher.Constraint('Distance', 1, w))
            else:
                sketch.addGeometry(Part.Circle(App.Vector(0,0,0), App.Vector(0,0,1), item['radius']), False)
                sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 3, -1, 1))
                sketch.addConstraint(Sketcher.Constraint('Radius', 0, item['radius']))
            obj = body.newObject('PartDesign::Pad', name)
            obj.Profile, obj.Length = sketch, item['height']
            doc.recompute()
            sketches.append(dict(name=sketch.Name, fullyConstrained=bool(sketch.FullyConstrained)))
            # Use the Body shape to include its placement in subsequent booleans.
            features[name] = body
        elif op == 'hole':
            drill = doc.addObject('Part::Cylinder', name + 'Tool')
            drill.Radius, drill.Height, drill.Placement.Base = item['radius'], item['height'], p
            obj = doc.addObject('Part::Cut', name)
            obj.Base, obj.Tool = features[item['base']], drill
        else:
            obj = doc.addObject({'cut':'Part::Cut','fuse':'Part::Fuse','common':'Part::Common'}[op], name)
            obj.Base, obj.Tool = features[item['base']], features[item['tool']]
        if op != 'sketch_pad': features[name] = obj
        doc.recompute()
        if features[name].Shape.isNull() or not features[name].Shape.isValid():
            raise ValueError('Invalid or empty shape at feature ' + name)
    result = doc.addObject('Part::Feature', 'HarnessResult')
    result.Shape = features[recipe['result']].Shape
    doc.recompute()
    return doc, result, sketches

try:
    version = '.'.join(App.Version()[:3])
    if version != '1.1.4': raise ValueError('Expected FreeCAD 1.1.4; got ' + version)
    runtime_paths = {key: App.ConfigGet(key) for key in ['UserConfigPath', 'UserAppData', 'UserCachePath', 'UserMacroPath', 'AppTempPath']}
    if any(os.path.commonpath([ROOT, os.path.realpath(value)]) != ROOT for value in runtime_paths.values()):
        raise ValueError('FreeCAD user paths escaped the Action directory')
    if request['operation'] == 'verify':
        doc, result = safe_document(os.path.join(ROOT, 'model.FCStd'))
        native = metrics(result.Shape)
        shape = Part.Shape()
        shape.read(os.path.join(ROOT, 'model.step'))
        step = metrics(shape)
        mesh = Mesh.Mesh(os.path.join(ROOT, 'model.stl'))
        sketches = [dict(name=o.Name, fullyConstrained=bool(o.FullyConstrained)) for o in doc.Objects if o.TypeId == 'Sketcher::SketchObject']
        write('readback.json', dict(version=version, runtimePaths=runtime_paths, native=native, step=step, mesh=dict(facets=mesh.CountFacets), sketches=sketches))
    else:
        if request['operation'] == 'build': doc, result, sketches = build(request['recipe'])
        else:
            doc, result = safe_document(request['file'])
            sketches = []
        original = metrics(result.Shape)
        doc.recompute()
        doc.saveAs(os.path.join(ROOT, 'model.FCStd'))
        result.Shape.exportStep(os.path.join(ROOT, 'model.step'))
        # Fixed tessellation with bounded complexity; no renderer-side native execution.
        vertices, triangles = result.Shape.tessellate(0.15)
        if len(triangles) > 100000: raise ValueError('CAD mesh exceeds 100000 triangles')
        mesh = Mesh.Mesh([(vertices[a],vertices[b],vertices[c]) for a,b,c in triangles])
        mesh.write(os.path.join(ROOT, 'model.stl'))
        write('build.json', dict(version=version, runtimePaths=runtime_paths, original=original, sketches=sketches))
    sys.stdout.flush()
    os._exit(0)
except BaseException as error:
    write('error.json', dict(error=str(error), traceback=traceback.format_exc()[-8192:]))
    sys.stdout.flush()
    os._exit(1)
