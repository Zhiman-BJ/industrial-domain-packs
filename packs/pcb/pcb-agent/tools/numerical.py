"""Converged finite-volume PCB DC conduction and steady-state board temperature."""
import copy,math
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve
from shapely import contains_xy
from shapely.geometry import Point
from shapely.ops import unary_union
from tools import cad_geometry as cg


def positive(x):
    if type(x) not in (int,float) or not math.isfinite(x) or x<=0:raise ValueError('Explicit positive finite physical parameter required')
    return x


def grid(shape,step,max_cells=350000):
    positive(step);x0,y0,x1,y1=shape.bounds
    if type(max_cells)!=int or not 1<=max_cells<=16000000:
        raise ValueError('max_mesh_cells must be an integer in 1..16000000')
    nx,ny=math.ceil((x1-x0)/step),math.ceil((y1-y0)/step)
    if nx*ny>max_cells:raise ValueError('Mesh exceeds configured cell budget per layer; declare an adequate bounded max_mesh_cells or refine the analysis scope')
    x=x0+(np.arange(nx)+.5)*step;y=y0+(np.arange(ny)+.5)*step;xx,yy=np.meshgrid(x,y)
    return xx,yy,contains_xy(shape,xx,yy)


def system(masks,gxy,vertical=(),sink=0,edge_masks=None):
    ids=np.full(masks.shape,-1,dtype=int);ids[masks]=np.arange(masks.sum());n=int(masks.sum())
    if n==0 or n>600000:raise ValueError('Empty or oversized numerical system')
    rows=[];cols=[];values=[];diagonal=np.full(n,sink,dtype=float)
    def edges(i,j,g):
        i,j=np.asarray(i),np.asarray(j);g=np.broadcast_to(g,i.shape)
        good=(i>=0)&(j>=0)&(i!=j)
        i,j,g=i[good],j[good],g[good]
        if not np.isfinite(g).all() or np.any(g<0):raise ValueError('Invalid mesh conductance')
        np.add.at(diagonal,i,g);np.add.at(diagonal,j,g)
        rows.extend((i,j));cols.extend((j,i));values.extend((-g,-g))
    for k in range(masks.shape[0]):
        for direction,(a,b) in enumerate(((np.s_[:,:-1],np.s_[:,1:]),(np.s_[:-1,:],np.s_[1:,:]))):
            g=np.broadcast_to(gxy[k][direction],ids[k][a].shape)
            if edge_masks is not None and k in edge_masks:g=g*edge_masks[k][direction]
            edges(ids[k][a],ids[k][b],g)
    if vertical:
        a,b,g=zip(*vertical)
        edges(ids[tuple(np.array(a).T)],ids[tuple(np.array(b).T)],np.array(g))
    rows.append(np.arange(n));cols.append(np.arange(n));values.append(diagonal)
    matrix=coo_matrix((np.concatenate(values),(np.concatenate(rows),np.concatenate(cols))),shape=(n,n))
    return matrix.tolil(),ids


def solve(matrix,rhs,fixed=None):
    original=matrix.tocsr();load=rhs.copy()
    if fixed:
        for row,value in fixed.items():matrix.rows[row]=[row];matrix.data[row]=[1.];rhs[row]=value
    solution=spsolve(matrix.tocsr(),rhs)
    if not np.isfinite(solution).all():raise ValueError('Singular/disconnected mesh or solver did not converge')
    residual=original@solution-load
    free=np.ones(len(rhs),dtype=bool)
    if fixed:free[list(fixed)]=False
    error=float(np.max(np.abs(residual[free]))) if np.any(free) else 0.
    if error>1e-7*max(1,float(np.max(np.abs(load)))):raise ValueError('Excessive linear-solve residual')
    return solution,residual,error


def pi_once(geo,p,step):
    net=p['net'];rho=positive(p['resistivity_ohm_m']);thickness=p['copper_thickness_mm']
    layer_names=geo['layers']
    if set(thickness)!=set(layer_names):raise ValueError('Explicit copper thickness required for every enabled layer')
    region=unary_union([shape for (name,layer),shape in geo['copper'].items() if name==net])
    if region.is_empty:raise ValueError('No native copper for requested PI net: '+net)
    # Empty board area need not occupy the fine conduction grid. Never crop
    # copper on the selected net or discard disconnected islands silently.
    xx,yy,_=grid(region,step,p.get('max_mesh_cells',350000))
    masks=np.array([contains_xy(geo['copper'].get((net,k),Point().buffer(0)),xx,yy) for k in layer_names])
    gxy=[(positive(thickness[k])*1e-3/rho,)*2 for k in layer_names];vertical=[]
    def cell(xy):return tuple(np.unravel_index(np.argmin((xx-xy[0])**2+(yy-xy[1])**2),xx.shape))
    for via in geo['barrels']:
        if via['net']!=net:continue
        z=p['layer_z_mm'];plating=positive(p['plating_mm']);r,c=cell(via['xy'])
        first,last=layer_names.index(via['top']),layer_names.index(via['bottom'])
        # A hole centre is void. Contact each layer at the nearest annular cell.
        for k in range(first,last):
            contacts=[]
            for layer in (k,k+1):
                distance=np.where(masks[layer],(xx-via['xy'][0])**2+(yy-via['xy'][1])**2,np.inf)
                rr,cc=np.unravel_index(np.argmin(distance),distance.shape)
                if distance[rr,cc]>(via['drill_mm']/2+2*step)**2:raise ValueError('Via annulus unresolved in mesh')
                contacts.append((layer,rr,cc))
            length=abs(z[layer_names[k]]-z[layer_names[k+1]])*1e-3
            conductance=math.pi*via['drill_mm']*plating*1e-6/(rho*positive(length))
            vertical.append((*contacts,conductance))
    matrix,ids=system(masks,gxy,vertical);rhs=np.zeros(int(masks.sum()))
    def cells(anchor):
        pad=geo['pads'][anchor]
        if pad['net']!=net:raise ValueError('PI terminal net mismatch')
        hit=np.zeros(masks.shape,dtype=bool)
        for k,name in enumerate(layer_names):
            if name in pad['layers']:hit[k]=contains_xy(pad['layers'][name],xx,yy)&masks[k]
        result=ids[hit]
        if len(result)==0:raise ValueError('PI pad not resolved by mesh')
        return result
    supply=positive(p['voltage_v']);fixed={int(i):supply for i in cells(p['source_pad'])};loads={}
    for load in p['loads']:
        current=positive(load['current_a']);indices=cells(load['pad'])
        if any(i in fixed for i in indices):raise ValueError('Load overlaps source boundary')
        rhs[indices]-=current/len(indices);loads[load['pad']]=(indices,current)
    if not loads:raise ValueError('Explicit DC loads required')
    voltage,residual,error=solve(matrix,rhs,fixed)
    current=sum(load['current_a'] for load in p['loads']);observed=float(sum(residual[i] for i in fixed))
    if abs(current-observed)>1e-6*max(current,1e-6):raise ValueError('Current conservation failed')
    loss=sum(float(np.mean(supply-voltage[indices]))*i for indices,i in loads.values())
    return dict(drop_max_v=float(max(np.max(supply-voltage[idx]) for idx,i in loads.values())),loss_w=loss,
                effective_resistance_ohm=loss/current**2,source_current_a=observed,residual_a=error,nodes=int(masks.sum()))


def thermal_once(geo,p,step):
    conductivity=p['conductivity_w_mk'];kx,ky=map(positive,conductivity)
    thickness=positive(p['thickness_mm'])*1e-3;h=positive(p['convection_w_m2k']);ambient=p['ambient_c']
    if type(ambient) not in (int,float) or not math.isfinite(ambient) or ambient<=-273.15:raise ValueError('Invalid ambient temperature')
    sources=p['sources'];omissions=p['zero_power_components']
    if set(sources)|set(omissions)!=set(geo['parts']) or set(sources)&set(omissions) or any(not str(v).strip() for v in omissions.values()):
        raise ValueError('Every component needs a heat source or explicit zero-power reason')
    xx,yy,mask=grid(geo['outline'],step,p.get('max_mesh_cells',350000));sink=2*h*(step*1e-3)**2
    matrix,ids=system(mask[None,:,:],[(kx*thickness,ky*thickness)],sink=sink)
    rhs=np.full(int(mask.sum()),sink*ambient);power=0.
    for ref,source in sources.items():
        watts=positive(source['watts']);radius=positive(source['radius_mm']);power+=watts
        spot=Point(geo['parts'][ref]).buffer(radius);hit=contains_xy(spot,xx,yy)&mask
        if not np.any(hit):raise ValueError('Heat source not resolved on PCB mesh')
        indices=ids[0][hit];rhs[indices]+=watts/len(indices)
    if not power:raise ValueError('Nonzero explicit heat load required')
    temperature,residual,error=solve(matrix,rhs)
    rejected=float(np.sum((temperature-ambient)*sink));balance=abs(rejected-power)/power
    if balance>1e-6:raise ValueError('Thermal power balance failed')
    return dict(max_board_temperature_c=float(np.max(temperature)),mean_board_temperature_c=float(np.mean(temperature)),
                rise_max_c=float(np.max(temperature)-ambient),rejected_power_w=rejected,power_balance_rel=balance,nodes=int(mask.sum()))


def run(category,board,p):
    geo=cg.extract(board);step=positive(p['mesh_mm']);tolerance=positive(p['convergence_rel'])
    if tolerance>.2:raise ValueError('Mesh convergence tolerance must be at most 20%')
    if category=='pi':
        if geo['minimum_width_mm'] and step>geo['minimum_width_mm']/3:raise ValueError('PI mesh must resolve the narrowest trace with at least three cells')
        fn=pi_once;metric='effective_resistance_ohm'
    elif category=='thermal':
        fn=thermal_once;metric='rise_max_c'
        if 'layered_stack' in p:
            from tools.thermal_layers import once
            fn=once
    else:raise ValueError('Unsupported finite-volume category')
    fine_parameters=p
    if category=='thermal' and 'layered_stack' in p:
        fine_parameters=copy.deepcopy(p)
        fine_parameters['layered_stack']['dielectric_slices']*=2
    coarse=fn(geo,p,step);fine=fn(geo,fine_parameters,step/2)
    delta=abs(fine[metric]-coarse[metric])/max(abs(fine[metric]),1e-12)
    details=dict(metric=metric,relative_change=delta,limit=tolerance,mesh_mm=[step,step/2],coarse=coarse,fine=fine)
    if category=='thermal' and 'layered_stack' in p:
        contacts={key:abs(value-coarse[key])/max(abs(value-p['ambient_c']),1e-12)
                  for key,value in fine.items() if key.endswith(('_contact_max_c','_contact_mean_c'))}
        delta=max(delta,*contacts.values())
        details.update(relative_change=delta,contact_relative_changes=contacts,
                       dielectric_slices=[p['layered_stack']['dielectric_slices'],fine_parameters['layered_stack']['dielectric_slices']])
    return dict(status='PASS' if delta<=tolerance else 'UNKNOWN',method='scipy finite-volume '+category,
                measurements=fine,convergence=details,
                issues=[] if delta<=tolerance else ['Mesh refinement did not converge within requested tolerance'],
                scope=('DC copper conduction only; no transient/AC PDN' if category=='pi' else
                       'Steady native copper/dielectric sheets, plated barrels and two-face convection; ideal bonded contacts, no junction, enclosure, airflow, radiation or temperature feedback' if 'layered_stack' in p else
                       'Steady-state anisotropic effective board conduction with two-face convection; no component junction or enclosure model'))
