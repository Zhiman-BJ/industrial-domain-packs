"""Native copper -> converged multi-terminal DC resistor network for SPICE.

Finite-volume sheet conduction, equipotential pad contacts and plated barrels.
No inductance, capacitance, skin effect, temperature feedback or SI claim.
"""
import math
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu
from shapely import contains_xy, covers, linestrings
from shapely.geometry import Polygon
from shapely.ops import unary_union
from tools import cad_geometry as cg, numerical


class ExtractionError(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence=evidence


def validate(p):
    required = {'mode', 'resistivity_ohm_m', 'copper_thickness_mm', 'layer_z_mm',
                'plating_mm', 'mesh_mm', 'convergence_rel', 'ideal_nets'}
    if not isinstance(p, dict) or set(p)-required-{'max_mesh_cells','max_refinements'} or required-set(p):
        raise ValueError('pcb_parasitics requires explicit material, layer, plating, mesh, convergence and ideal_nets parameters')
    if p['mode'] != 'dc_resistance':raise ValueError('Only dc_resistance copper extraction is implemented')
    for k in ('resistivity_ohm_m', 'plating_mm', 'mesh_mm', 'convergence_rel'):
        numerical.positive(p[k])
    if p['convergence_rel'] > .2:raise ValueError('Copper extraction convergence limit must be <= 20%')
    if type(p.get('max_refinements',2)) is not int or not 1<=p.get('max_refinements',2)<=4:
        raise ValueError('max_refinements must be an integer in 1..4')
    for k in ('copper_thickness_mm', 'layer_z_mm'):
        if not isinstance(p[k], dict) or not p[k]:raise ValueError('Explicit layer values required: '+k)
        for value in p[k].values():
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Finite layer values required')
            if k == 'copper_thickness_mm':numerical.positive(value)
    if not isinstance(p['ideal_nets'], dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v.strip() for k,v in p['ideal_nets'].items()):
        raise ValueError('Each explicitly ideal net requires a justification')


def _edges(shape, xx, yy):
    result=[]
    for a,b in ((np.s_[:,:-1], np.s_[:,1:]), (np.s_[:-1,:], np.s_[1:,:])):
        points=np.stack((np.stack((xx[a],yy[a]),axis=-1),np.stack((xx[b],yy[b]),axis=-1)),axis=-2)
        result.append(covers(shape, linestrings(points)))
    return tuple(result)


def _once(geo, net, ports, p, step):
    layers=geo['layers']; rho=p['resistivity_ohm_m']
    shapes=[geo['copper'].get((net,k),Polygon()) for k in layers]
    region=unary_union(shapes)
    if region.is_empty:raise ValueError('No native copper on '+net)
    xx,yy,_=numerical.grid(region,step,p.get('max_mesh_cells',350000))
    masks=np.array([contains_xy(shape,xx,yy) for shape in shapes])
    edge_masks={k:_edges(shape,xx,yy) for k,shape in enumerate(shapes)}
    vertical=[]
    for via in geo['barrels']:
        if via['net']!=net:continue
        first,last=layers.index(via['top']),layers.index(via['bottom'])
        contacts={}
        for k in range(first,last+1):
            # Require contact at this actual barrel, not the closest remote trace.
            radius=np.sqrt((xx-via['xy'][0])**2+(yy-via['xy'][1])**2)
            hits=np.argwhere(masks[k] & (radius <= via['drill_mm']/2+step*1.5))
            if len(hits):contacts[k]=[tuple(int(n) for n in row) for row in hits]
        ks=sorted(contacts)
        if len(ks)<2:raise ValueError('Barrel contacts unresolved: '+via['id'])
        for a,b in zip(ks,ks[1:]):
            length=abs(p['layer_z_mm'][layers[b]]-p['layer_z_mm'][layers[a]])*1e-3
            g=math.pi*via['drill_mm']*p['plating_mm']*1e-6/(rho*numerical.positive(length))
            # Use the nearest contact per face for the barrel approximation.
            def nearest(k):return min(contacts[k],key=lambda rc:radius[rc])
            vertical.append(((a,*nearest(a)),(b,*nearest(b)),g))
    gxy=[(p['copper_thickness_mm'][k]*1e-3/rho,)*2 for k in layers]
    matrix,ids=numerical.system(masks,gxy,vertical,edge_masks=edge_masks)
    matrix=matrix.tocsr();n=matrix.shape[0];m=len(ports)
    mapping=np.full(n,-1,dtype=int)
    for index,port in enumerate(ports):
        pad=geo['pads'][port];contact=np.zeros(masks.shape,dtype=bool)
        for k,layer in enumerate(layers):
            if layer in pad['layers']:contact[k]=contains_xy(pad['layers'][layer],xx,yy)&masks[k]
        cells=ids[contact]
        if not len(cells):raise ValueError('Unresolved pad contact: '+port)
        if np.any(mapping[cells]>=0):raise ValueError('Distinct port pads physically overlap; explicit shared-contact model required')
        mapping[cells]=index
    free=np.flatnonzero(mapping<0);mapping[free]=np.arange(m,m+len(free))
    coo=matrix.tocoo()
    reduced=coo_matrix((coo.data,(mapping[coo.row],mapping[coo.col])),shape=(m+len(free),)*2).tocsr()
    reduced.eliminate_zeros()
    components,labels=connected_components(reduced,directed=False)
    if len(set(labels[:m]))!=1:raise ValueError('Disconnected physical copper terminals on '+net)
    # Floating islands have no terminal and no DC influence; retain their count.
    keep=np.flatnonzero(labels==labels[0]);reduced=reduced[keep][:,keep]
    pp=reduced[:m,:m].toarray()
    if reduced.shape[0]>m:
        ip=reduced[m:,:m].toarray();ii=reduced[m:,m:].tocsc()
        solution=splu(ii).solve(ip)
        residual=np.max(np.abs(ii@solution-ip))
        if residual>1e-7*max(1,np.max(np.abs(ip))):raise ValueError('Copper reduction residual exceeded')
        admittance=pp-reduced[:m,m:]@solution
    else:admittance=pp
    scale=max(1,float(np.max(np.abs(admittance))))
    if (not np.isfinite(admittance).all() or np.max(np.abs(admittance-admittance.T))>scale*1e-7
            or np.max(np.abs(admittance.sum(axis=1)))>scale*1e-7):
        raise ValueError('Copper reduction failed reciprocity/current conservation')
    conductances={}
    for i in range(m):
        for j in range(i+1,m):
            g=-float((admittance[i,j]+admittance[j,i])/2)
            if g < -scale*1e-9:raise ValueError('Non-passive copper reduction')
            if g>scale*1e-12:conductances[(i,j)]=g
    # Compare terminal resistance, not a conductance norm hiding a weak path.
    inverse=np.linalg.inv(admittance[:-1,:-1])
    resistances={}
    for i in range(m):
        for j in range(i+1,m):
            q=np.zeros(m-1)
            if i<m-1:q[i]=1
            if j<m-1:q[j]-=1
            resistance=float(q@inverse@q)
            if not math.isfinite(resistance) or resistance<=0:raise ValueError('Invalid terminal resistance')
            resistances[(i,j)]=resistance
    return conductances,resistances,dict(mesh_nodes=n,floating_mesh_components=components-1)


def extract(board, actual, parameters):
    validate(parameters);p=parameters;geo=cg.extract(board)
    layers=geo['layers']
    if set(p['copper_thickness_mm'])!=set(layers) or set(p['layer_z_mm'])!=set(layers):
        raise ValueError('Extraction stack must cover every enabled copper layer')
    z=[p['layer_z_mm'][k] for k in layers]
    if len(z)>1 and not (all(a<b for a,b in zip(z,z[1:])) or all(a>b for a,b in zip(z,z[1:]))):
        raise ValueError('Physical layer positions must be strictly ordered')
    step=p['mesh_mm']
    if geo['minimum_width_mm'] and step>geo['minimum_width_mm']/3:
        raise ValueError('Copper extraction needs at least three cells across the narrowest track')
    by_net={};pins={}
    for key,pad in sorted(geo['pads'].items()):
        net=pad['net']
        if not net:continue
        if len(pad.get('members',[]))!=1:
            raise ValueError('Repeated physical pads require an explicit shared-terminal conductor model: '+key)
        if actual['pins'].get(key)!=net:raise ValueError('Native SCH/PCB pad net mismatch: '+key)
        by_net.setdefault(net,[]).append(key)
    if set(p['ideal_nets'])-set(by_net):raise ValueError('Ideal-net declaration does not identify native copper')
    # Cross-net shorts must not disappear when extracting each named net alone.
    for layer in layers:
        nets=[(net,shape) for (net,k),shape in geo['copper'].items() if k==layer]
        for i,(a,sa) in enumerate(nets):
            for b,sb in nets[i+1:]:
                if sa.intersects(sb):raise ValueError('Different or unassigned nets touch on '+layer+': '+a+', '+b)
    resistors=[];reports={}
    for net,ports in sorted(by_net.items()):
        if net in p['ideal_nets']:continue
        if len(ports)>32:raise ValueError('More than 32 terminals per net requires a larger qualified reduction budget')
        for port in ports:pins[port]='pcb_p'+str(len(pins))
        if len(ports)<2:continue
        coarse_g,coarse_r,coarse_info=_once(geo,net,ports,p,step)
        trials=[];mesh=step
        for _ in range(p.get('max_refinements',2)):
            fine_g,fine_r,fine_info=_once(geo,net,ports,p,mesh/2)
            delta=max(abs(fine_r[k]-coarse_r[k])/fine_r[k] for k in fine_r)
            trials.append(dict(mesh_mm=[mesh,mesh/2],relative_change=delta,
                               coarse=coarse_info,fine=fine_info))
            if delta<=p['convergence_rel']:break
            coarse_g,coarse_r,coarse_info=fine_g,fine_r,fine_info;mesh/=2
        reports[net]=dict(ports=ports,relative_change=delta,limit=p['convergence_rel'],refinements=trials,
                         terminal_resistances=[dict(a=ports[i],b=ports[j],ohm=r) for (i,j),r in fine_r.items()])
        if delta>p['convergence_rel']:
            raise ExtractionError('Copper resistance mesh has not converged on '+net+': '+str(delta),
                                  dict(status='UNKNOWN',nets=reports,parameters=p))
        for (i,j),g in fine_g.items():
            resistors.append(dict(a=pins[ports[i]],b=pins[ports[j]],ohm=1/g,net=net))
    if not resistors:raise ValueError('No multi-terminal native copper network was extracted')
    return dict(pin_nodes=pins,resistors=resistors,nets=reports,ideal_nets=p['ideal_nets'],
                method='native copper polygons, finite-volume DC conduction, terminal Schur reduction',
                scope='DC resistance only; pads are equipotential, barrels are lumped. No capacitance, inductance, skin effect, full SI or junction thermal claim.')
