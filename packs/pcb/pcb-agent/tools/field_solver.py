"""Actual PCB copper → openEMS 3D FDTD ports and radiated fields.

This adapter models explicitly excited bare-board interconnects with PEC copper.
It does not infer active-device switching, cable/enclosure geometry or immunity.
"""
from pathlib import Path
import contextlib,ctypes,math,os,re
import numpy as np
from shapely.geometry import Point, box
from shapely.ops import triangulate
from tools import cad_geometry as cg
from tools.numerical import positive


def _triangles(shape):
    pieces=[shape] if shape.geom_type=='Polygon' else list(shape.geoms)
    for piece in pieces:
        if piece.is_empty:continue
        if piece.geom_type!='Polygon':raise ValueError('Unsupported nonpolygon copper')
        if not piece.interiors:
            yield np.array(piece.exterior.coords[:-1]).T
        else:
            for tri in triangulate(piece):
                if piece.covers(tri):yield np.array(tri.exterior.coords[:-1]).T


@contextlib.contextmanager
def native_log(path):
    # FDTD writes through C stdout/stderr; Python redirect_stdout is insufficient.
    libc=ctypes.CDLL(None);libc.fflush(None);saved=[os.dup(1),os.dup(2)]
    with open(path,'w') as log:
        try:
            os.dup2(log.fileno(),1);os.dup2(log.fileno(),2);yield
        finally:
            libc.fflush(None)
            for fd,copy in zip((1,2),saved):os.dup2(copy,fd);os.close(copy)


def frequencies(parameters):
    """An explicit sampled band, or the existing single-frequency contract."""
    if 'frequencies_hz' in parameters:
        if 'measure_hz' in parameters:raise ValueError('Choose frequencies_hz or measure_hz, not both')
        values=parameters['frequencies_hz']
        if not isinstance(values,list) or not 2<=len(values)<=201:
            raise ValueError('frequencies_hz requires 2..201 increasing samples')
        values=[positive(value) for value in values]
        if values!=sorted(set(values)):raise ValueError('Frequency samples must be unique and increasing')
    else:values=[positive(parameters['measure_hz'])]
    center=positive(parameters['center_hz']);bandwidth=positive(parameters['bandwidth_hz'])
    if any(not max(0,center-bandwidth)<value<center+bandwidth for value in values):
        raise ValueError('Every measurement frequency must lie inside excitation bandwidth')
    return values


def refinement_change(first,second,metric):
    if first['frequencies_hz']!=second['frequencies_hz']:raise ValueError('Convergence samples differ')
    if any(metric not in row for row in second['spectral_measurements']):
        raise ValueError('Choose an actually calculated convergence metric')
    changes=[abs(b[metric]-a[metric])/max(abs(b[metric]),1e-12)
             for a,b in zip(first['spectral_measurements'],second['spectral_measurements'])]
    return max(changes),changes


def port_extent(pad,port,reference,xy):
    """A finite source/resistor area entirely contacting signal and reference."""
    contact=pad['layers'][port['signal_layer']].intersection(reference)
    point=Point(xy)
    if pad['net']==port['reference_net'] or not contact.contains(point):
        raise ValueError('Port needs distinct signal/reference nets and an interior contact')
    size=port.get('extent_mm')
    if size is None:
        # Conservative inscribed square, determined once from native geometry;
        # mesh refinement must not change the physical port definition.
        side=math.sqrt(2)*point.distance(contact.boundary)*.9
        size=[side,side]
    if not isinstance(size,list) or len(size)!=2:
        raise ValueError('Port extent_mm requires [width,height]')
    width,height=[positive(value) for value in size]
    x,y=xy;bounds=[x-width/2,y-height/2,x+width/2,y+height/2]
    if not contact.covers(box(*bounds)):
        raise ValueError('Finite port area must be inside both native pad and reference copper')
    return bounds


def anchored_mesh(low,high,anchors,step):
    """Subdivide between fixed features, avoiding sliver cells from grid insertion."""
    # Native geometry is exported on a 1e-7 mm grid below. Collapse equivalent
    # pad features at that same precision before testing sliver-cell spacing.
    low,high=round(float(low),7),round(float(high),7)
    fixed=sorted(set(round(float(x),7) for x in [low,high,*anchors]))
    if fixed[0]<low or fixed[-1]>high or any(b-a<step/100 for a,b in zip(fixed,fixed[1:])):
        raise ValueError('Distinct required mesh features are too close or outside the domain')
    lines=[]
    for a,b in zip(fixed,fixed[1:]):
        lines.extend(np.linspace(a,b,math.ceil((b-a)/step)+1)[:-1])
    return [*lines,fixed[-1]]


def copper_edge_anchors(geo):
    """Fix rectilinear copper boundaries across refinement levels.

    Curved/diagonal boundaries remain staircased and still need convergence.
    Never move or merge distinct physical edges to force a solver PASS.
    """
    axes={'x':set(),'y':set()}
    for shape in [geo['board_outline'],*geo['copper'].values()]:
        polygons=[shape] if shape.geom_type=='Polygon' else list(shape.geoms)
        for poly in polygons:
            if poly.is_empty:continue
            if poly.geom_type!='Polygon':raise ValueError('Copper mesh requires polygon geometry')
            for ring in [poly.exterior,*poly.interiors]:
                points=[tuple(round(float(v),7) for v in p) for p in ring.coords]
                for a,b in zip(points,points[1:]):
                    if a[0]==b[0] and a[1]!=b[1]:axes['x'].add(a[0])
                    if a[1]==b[1] and a[0]!=b[0]:axes['y'].add(a[1])
    return {axis:sorted(values) for axis,values in axes.items()}


def graded_air_mesh(edge, direction, margin, step, maximum, pml=False):
    """Grow air cells away from native geometry, with unchanged domain bounds.

    All cell sizes (including the exterior cap) scale during mesh refinement.
    The final PML cells must be uniform; inadequate air margins are rejected.
    """
    step,maximum,margin=positive(step),positive(maximum),positive(margin)
    if direction not in (-1,1) or maximum<step:raise ValueError('Air mesh cap must be at least the interior cell size')
    widths=[];total=0.;width=step
    while total<margin:
        widths.append(width);total+=width;width=min(maximum,width*1.3)
        if len(widths)>100000:raise ValueError('Air mesh exceeds axis budget')
    if pml and (len(widths)<10 or any(abs(w-maximum)>1e-9 for w in widths[-8:])):
        raise ValueError('Air margin cannot contain graded transition and eight uniform PML cells')
    # Rescale all cells rather than leaving a tiny last cell at the boundary.
    scale=margin/total;distance=0.;lines=[float(edge)]
    for width in widths:
        distance+=width*scale;lines.append(float(edge)+direction*distance)
    lines[-1]=float(edge)+direction*margin
    return sorted(lines)


def validate_native_ports(text):
    # Parallel pure-R ports are installed by Operator::Calc_LumpedElements,
    # not the Series/Parallel RLC extension: its Active cells: 0 is normal.
    if re.search(r'Operator::Calc_LumpedElements\(\): Warning:|Unused primitive[^\n]*port_[^\n]*(?:resist|excite)',text):
        raise ValueError('Native FDTD skipped or could not resolve a required port primitive')


def once(geo,p,out,step,radiation):
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS
    from openEMS.physical_constants import C0
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    z=p['layer_z_mm']
    if set(z)!=set(geo['layers']) or len(set(z.values()))!=len(z):raise ValueError('Distinct physical z coordinates for every copper layer required')
    zs=[z[k] for k in geo['layers']]
    if not (zs==sorted(zs) or zs==sorted(zs,reverse=True)):raise ValueError('Layer z coordinates must follow native layer order')
    z0,z1=min(zs),max(zs);margin=positive(p['air_margin_mm']);f0=positive(p['center_hz']);fc=positive(p['bandwidth_hz'])
    samples=frequencies(p)
    if geo['minimum_width_mm'] and step>geo['minimum_width_mm']/3:raise ValueError('FDTD needs at least three cells across the narrowest trace')
    omissions=p['component_omissions']
    if set(omissions)!=set(geo['parts']) or any(not isinstance(r,str) or not r.strip() for r in omissions.values()):raise ValueError('Bare-board field scope requires an explicit omission reason for every component body/circuit model')
    boundary=p['boundary']
    if boundary not in ('MUR','PML_8'):raise ValueError('Explicit absorbing boundary MUR or PML_8 required')
    steps=p['max_timesteps'];end=positive(p['end_energy_rel'])
    if type(steps)!=int or not 100<=steps<=1000000 or end>1e-3:raise ValueError('FDTD timestep/energy criterion invalid')
    fdtd=openEMS(NrTS=steps,EndCriteria=end);fdtd.SetGaussExcite(f0,fc);fdtd.SetBoundaryCond([boundary]*6)
    csx=ContinuousStructure();fdtd.SetCSX(csx);mesh=csx.GetGrid();mesh.SetDeltaUnit(1e-3)
    metal=csx.AddMetal('pcb_PEC')
    for (net,layer),shape in geo['copper'].items():
        for poly in _triangles(shape):metal.AddPolygon(poly,'z',z[layer],priority=10)
    for via in geo['barrels']:
        plating=positive(p['plating_mm']);a,b=sorted((z[via['top']],z[via['bottom']]))
        ring=Point(via['xy']).buffer(via['drill_mm']/2+plating,resolution=16).difference(Point(via['xy']).buffer(via['drill_mm']/2,resolution=16))
        for poly in _triangles(ring):metal.AddLinPoly(poly,'z',a,b-a,priority=10)
    intervals=[];max_epsilon=1
    for i,diel in enumerate(p['dielectrics']):
        low,high=diel['z0_mm'],diel['z1_mm'];er=positive(diel['epsilon_r']);loss=diel['conductivity_s_m']
        if not z0<=low<high<=z1 or type(loss) not in (int,float) or not math.isfinite(loss) or loss<0:raise ValueError('Invalid dielectric interval or conductivity')
        intervals.append((low,high));max_epsilon=max(max_epsilon,er)
        material=csx.AddMaterial('dielectric_'+str(i),epsilon=er,kappa=loss)
        for poly in _triangles(geo['outline']):material.AddLinPoly(poly,'z',low,high-low,priority=0)
    ordered=sorted(intervals)
    if not ordered or abs(ordered[0][0]-z0)>1e-6 or abs(ordered[-1][1]-z1)>1e-6 or any(abs(a[1]-b[0])>1e-6 for a,b in zip(ordered,ordered[1:])):
        raise ValueError('Dielectrics must continuously cover the stack without overlaps')
    if step*1e-3>C0/(f0+fc)/math.sqrt(max_epsilon)/20:raise ValueError('Mesh coarser than wavelength/20')
    x0,y0,x1,y1=geo['board_outline'].bounds
    air_cap=None
    if 'air_mesh_mm' in p:
        nominal_step=positive(p['mesh_mm']);nominal_cap=positive(p['air_mesh_mm'])
        if nominal_cap<nominal_step:raise ValueError('air_mesh_mm must be at least mesh_mm')
        air_cap=nominal_cap*step/nominal_step
        if air_cap*1e-3>C0/(f0+fc)/20:raise ValueError('Air cells exceed wavelength/20')
    lines={'x':[], 'y':[], 'z':[]}
    if air_cap is None:
        lines['z']=list(np.linspace(z0-margin,z0,math.ceil(margin/step)+1))+list(np.linspace(z1,z1+margin,math.ceil(margin/step)+1))
    else:
        for edge,direction in ((z0,-1),(z1,1)):
            lines['z']+=graded_air_mesh(edge,direction,margin,step,air_cap,boundary=='PML_8')
    for low,high in ordered:lines['z']+=list(np.linspace(low,high,max(4,math.ceil((high-low)/step))+1))
    ports=[];excited=[];port_geometries=[]
    for i,port in enumerate(p['ports']):
        pad=geo['pads'][port['pad']];layer=port['signal_layer'];ground=port['reference_layer'];xy=pad['xy'];reference=geo['copper'].get((port['reference_net'],ground))
        if len(pad.get('members',[]))>1 or 'pad_member_uuid' in port:
            members=[member for member in pad.get('members',[]) if member['uuid']==port.get('pad_member_uuid')]
            if len(members)!=1:raise ValueError('FDTD port on a repeated pad requires an explicit pad_member_uuid')
            xy=members[0]['xy']
        if layer not in pad['layers'] or reference is None or not reference.covers(Point(xy)) or layer==ground:raise ValueError('Port must join a real signal pad to real reference copper below/above that anchor')
        excitation=port['excite']
        if excitation not in (0,1):raise ValueError('Use exactly one unit-amplitude excited port')
        if excitation:excited.append(i)
        xmin,ymin,xmax,ymax=port_extent(pad,port,reference,xy)
        start=[xmin,ymin,z[ground]];stop=[xmax,ymax,z[layer]]
        ports.append(fdtd.AddLumpedPort(i+1,positive(port['resistance_ohm']),start,stop,'z',excitation,priority=20))
        lines['x'].extend([xmin,xy[0],xmax]);lines['y'].extend([ymin,xy[1],ymax])
        port_geometries.append(dict(pad=port['pad'],start_mm=start,stop_mm=stop))
    if len(excited)!=1:raise ValueError('Exactly one excited port per FDTD test required')
    alignment=p.get('mesh_alignment','ports')
    if alignment not in ('ports','copper_edges'):raise ValueError('mesh_alignment must be ports or copper_edges')
    edge_anchors=copper_edge_anchors(geo) if alignment=='copper_edges' else {'x':[],'y':[]}
    for axis,values in edge_anchors.items():lines[axis].extend(values)
    for axis,low,high in (('x',x0,x1),('y',y0,y1)):
        if air_cap is None:lines[axis]=anchored_mesh(low-margin,high+margin,lines[axis],step)
        else:
            lines[axis]=anchored_mesh(low,high,lines[axis],step)
            for edge,direction in ((low,-1),(high,1)):
                lines[axis]+=graded_air_mesh(edge,direction,margin,step,air_cap,boundary=='PML_8')
    for axis,values in lines.items():
        values=sorted(set(round(float(x),7) for x in values))
        if min(np.diff(values))<step/100:raise ValueError('Port nearly coincides with grid; choose a compatible mesh to avoid unstable time steps')
        mesh.AddLine(axis,values)
    cells=math.prod(len(mesh.GetLines(d))-1 for d in ('x','y','z'))
    limit=p.get('max_mesh_cells',3000000)
    if type(limit)!=int or not 1<=limit<=12000000:raise ValueError('FDTD max_mesh_cells must be an integer in 1..12000000')
    if cells>limit:raise ValueError('FDTD exceeds declared cell budget; select a suitable bounded max_mesh_cells or scoped domain')
    nf=fdtd.CreateNF2FFBox() if radiation else None
    csx.Write2XML(str(out/'geometry.xml'))
    # The native Python wrapper changes cwd to its simulation directory. Keep
    # later candidate-relative reads and edits anchored to the caller's project,
    # including when the native solve raises.
    previous_directory=Path.cwd()
    try:
        with native_log(out/'native.log'):fdtd.Run(str(out),cleanup=False,numThreads=2,verbose=1,dump_statistics=True)
    finally:
        os.chdir(previous_directory)
    text=(out/'native.log').read_text()
    validate_native_ports(text)
    # Check native termination, not merely successful import/output-file creation.
    energies=[-float(x) for x in re.findall(r'\(\s*-\s*([\d.]+)\s*dB\)',text,re.I)]
    iterations=re.findall(r'Time for (\d+) iterations',text)
    end_reached=bool(iterations and int(iterations[-1])<steps and 'Max. number of timesteps was reached' not in text and 'abort' not in text.lower())
    statistics={f.name:f.read_text() for f in out.glob('*stats*.txt')}
    for port in ports:port.CalcPort(str(out),np.array(samples))
    drive=ports[excited[0]];inc=drive.uf_inc
    if not np.all(np.isfinite(inc)) or np.any(np.abs(inc)<1e-15):raise ValueError('No resolved port excitation at a measurement frequency')
    rows=[]
    for index,frequency in enumerate(samples):
        row={}
        for i,port in enumerate(ports):
            row['s'+str(i+1)+str(excited[0]+1)+'_mag']=float(abs(port.uf_ref[index]/inc[index]))
        impedance=drive.uf_tot[index]/drive.if_tot[index]
        row.update(input_resistance_ohm=float(impedance.real),input_reactance_ohm=float(impedance.imag))
        rows.append(row)
    if radiation:
        # Explicit accepted power normalizes far-field outputs; excitation
        # amplitude is dimensionless unless an absolute power is declared.
        if not np.all(np.isfinite(drive.P_acc)) or np.any(drive.P_acc<=0):raise ValueError('Nonpositive accepted power; far field normalization invalid')
        result=nf.CalcNF2FF(str(out),np.array(samples),np.arange(0,181,15),np.arange(0,361,30),radius=positive(p['observation_radius_m']),center=[(x0+x1)/2000,(y0+y1)/2000,(z0+z1)/2000])
        for index,row in enumerate(rows):
            scale=positive(p['accepted_power_w'])/float(drive.P_acc[index])
            row.update(radiated_power_w=float(result.Prad[index])*scale,directivity=float(result.Dmax[index]),
                       sampled_e_max_v_m=float(np.max(np.abs(result.E_norm[index])))*math.sqrt(scale))
    if not all(math.isfinite(value) for row in rows for value in row.values()):raise ValueError('Nonfinite FDTD measurements')
    measurements=rows[0] if len(samples)==1 else {
        key+'_'+suffix:float(fn(row[key] for row in rows)) for key in rows[0] for suffix,fn in [('min',min),('max',max)]}
    return dict(measurements=measurements,frequencies_hz=samples,spectral_measurements=rows,cells=cells,port_geometries=port_geometries,
                mesh_alignment=alignment,copper_edge_anchors_mm=edge_anchors,air_mesh_cap_mm=air_cap,
                energy_db=energies[-1] if energies else None,end_criterion_reached=end_reached,
                iterations=int(iterations[-1]) if iterations else None,statistics=statistics,native_tail=text[-2500:])


def run(category,board,p,out):
    geo=cg.extract(board);step=positive(p['mesh_mm']);tol=positive(p['convergence_rel'])
    if tol>.2:raise ValueError('Mesh convergence tolerance must be at most 20%')
    frequencies(p)
    domain=p.get('domain_convergence')
    if domain is not None:
        if not isinstance(domain,dict) or set(domain)!={'air_margin_mm','convergence_rel'}:
            raise ValueError('Domain convergence requires air_margin_mm and convergence_rel')
        if positive(domain['air_margin_mm'])<=p['air_margin_mm'] or positive(domain['convergence_rel'])>.2:
            raise ValueError('Domain check must expand the air boundary, with tolerance at most 20%')
    radiation=category=='emc';coarse=once(geo,p,Path(out)/'coarse',step,radiation)
    if not coarse['end_criterion_reached']:
        return dict(status='UNKNOWN',method='openEMS 3D full-wave FDTD from actual KiCad copper',
                    issues=['Coarse solve lacks native decay convergence; resolve its timestep/domain/excitation before refinement'],
                    measurements={},convergence=dict(coarse=coarse,refinement='NOT_RUN_COARSE_DECAY_FAILED'),
                    scope='Unconverged bare-board field diagnostic; no qualified spectral measurements')
    fine=once(geo,p,Path(out)/'fine',step/2,radiation)
    metric=p['convergence_metric']
    relative,changes=refinement_change(coarse,fine,metric)
    energy_ok=all(x['end_criterion_reached'] for x in (coarse,fine))
    issues=[]
    if relative>tol:issues.append('FDTD mesh refinement did not converge')
    if not energy_ok:issues.append('Native time-domain decay evidence is missing or above the requested threshold')
    convergence=dict(relative_change=relative,per_frequency_change=changes,frequencies_hz=fine['frequencies_hz'],limit=tol,metric=metric,coarse=coarse,fine=fine)
    if domain is not None:
        expanded=once(geo,dict(p,air_margin_mm=domain['air_margin_mm']),Path(out)/'expanded-domain',step/2,radiation)
        change,samples=refinement_change(fine,expanded,metric)
        if change>domain['convergence_rel']:issues.append('Expanded FDTD domain did not converge')
        if not expanded['end_criterion_reached']:issues.append('Expanded domain lacks native time-domain decay evidence')
        convergence['domain']=dict(relative_change=change,per_frequency_change=samples,limit=domain['convergence_rel'],expanded=expanded)
    return dict(status='UNKNOWN' if issues else 'PASS',method='openEMS 3D full-wave FDTD from actual KiCad copper',issues=issues,
                measurements=fine['measurements'],frequencies_hz=fine['frequencies_hz'],spectral_measurements=fine['spectral_measurements'],convergence=convergence,
                scope='Explicit port-driven PEC bare PCB at declared frequency samples; '+('radiation, no enclosure/cable/active-device or immunity model' if radiation else 'S-parameters, no conductor loss, eye diagram or active-device model'))
