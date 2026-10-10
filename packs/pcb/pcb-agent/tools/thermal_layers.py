"""Steady board conduction on native copper sheets and dielectric midplanes.

Declared material properties, perfect bonded contacts, no package junction,
enclosure, radiation or temperature feedback. This is a reduced sheet model,
not a general 3D package/airflow FEM solver.
"""
import math
import numpy as np
from shapely import contains_xy, covers, linestrings, prepare
from shapely.geometry import Point
from shapely.ops import unary_union
from tools.numerical import positive, grid, system, solve


def mesh(geo, p, step):
    """Explicit local windows refine physical contacts without cropping the board.

    Windows are fixed in board coordinates, so both convergence solves refine
    the same region. Orthogonal cell widths enter all areas and conductances.
    """
    positive(step)
    windows=p.get('mesh_windows',[])
    if not isinstance(windows,list) or len(windows)>32:
        raise ValueError('mesh_windows requires at most 32 bounded refinement windows')
    x0,y0,x1,y1=geo['outline'].bounds
    for window in windows:
        if not isinstance(window,dict) or set(window)!={'bounds_mm','refinement'}:
            raise ValueError('Mesh window requires bounds_mm and refinement')
        bounds=window['bounds_mm'];factor=window['refinement']
        if (not isinstance(bounds,list) or len(bounds)!=4
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in bounds)
                or not x0<=bounds[0]<bounds[2]<=x1 or not y0<=bounds[1]<bounds[3]<=y1
                or type(factor)!=int or not 2<=factor<=32):
            raise ValueError('Invalid mesh window bounds or refinement (integer 2..32)')
    if not windows:
        xx,yy,board=grid(geo['outline'],step,p.get('max_mesh_cells',350000))
        return xx,yy,board,np.full(xx.shape[1],step),np.full(xx.shape[0],step)
    limit=p.get('max_mesh_cells',350000)
    if type(limit)!=int or not 1<=limit<=16000000:
        raise ValueError('Locally refined thermal mesh exceeds bounded max_mesh_cells')
    def axis_segments(low,high,d):
        anchors=sorted({low,high,*[w['bounds_mm'][i] for w in windows for i in (d,d+2)]})
        segments=[];count=0
        for a,b in zip(anchors,anchors[1:]):
            factor=max([1,*[w['refinement'] for w in windows if w['bounds_mm'][d]<=(a+b)/2<=w['bounds_mm'][d+2]]])
            cells=(b-a)/step*factor
            if not math.isfinite(cells) or cells>limit:
                raise ValueError('Locally refined thermal mesh exceeds bounded max_mesh_cells')
            n=math.ceil(cells);segments.append((a,b,n));count+=n
        return segments,count
    xs,nx=axis_segments(x0,x1,0);ys,ny=axis_segments(y0,y1,1)
    if nx*ny>limit:
        raise ValueError('Locally refined thermal mesh exceeds bounded max_mesh_cells')
    def axis(segments,high):
        return np.concatenate([*[np.linspace(a,b,n+1)[:-1] for a,b,n in segments],[high]])
    x,y=axis(xs,x1),axis(ys,y1)
    xx,yy=np.meshgrid((x[:-1]+x[1:])/2,(y[:-1]+y[1:])/2)
    return xx,yy,contains_xy(geo['outline'],xx,yy),np.diff(x),np.diff(y)


def copper_edges(shape, xx, yy, mask):
    """Cell centres inside copper cannot bridge a native slot between them."""
    prepare(shape)
    edges=[]
    for a,b in ((np.s_[:,:-1],np.s_[:,1:]),(np.s_[:-1,:],np.s_[1:,:])):
        hit=mask[a]&mask[b];connected=np.zeros(hit.shape,dtype=bool)
        if np.any(hit):
            start=np.stack((xx[a][hit],yy[a][hit]),axis=-1)
            stop=np.stack((xx[b][hit],yy[b][hit]),axis=-1)
            connected[hit]=covers(shape,linestrings(np.stack((start,stop),axis=1)))
        edges.append(connected)
    return edges


def once(geo, p, step):
    if set(p) & {'conductivity_w_mk', 'thickness_mm', 'convection_w_m2k'}:
        raise ValueError('Choose layered_stack or homogeneous effective board parameters')
    stack = p['layered_stack']; names = geo['layers']; z = stack['layer_z_mm']
    thickness = stack['copper_thickness_mm']
    if len(names) < 2 or set(z) != set(names) or set(thickness) != set(names):
        raise ValueError('Physical positions and copper thickness required for every native layer')
    positions = [z[n] for n in names]
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in positions)
            or len(set(positions)) != len(positions)
            or not (positions == sorted(positions) or positions == sorted(positions, reverse=True))):
        raise ValueError('Distinct finite copper positions must follow native layer order')
    kc = positive(stack['copper_conductivity_w_mk'])
    cu = [positive(thickness[n])*1e-3 for n in names]
    dielectric = stack['dielectrics']
    if len(dielectric) != len(names)-1:
        raise ValueError('Exactly one dielectric between every adjacent copper layer required')
    gap = []; conductivities = []
    for k, material in enumerate(dielectric):
        if set(material) != {'layers', 'conductivity_w_mk'} or material['layers'] != names[k:k+2]:
            raise ValueError('Dielectric order must cover every adjacent pair exactly once')
        xyz = material['conductivity_w_mk']
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise ValueError('Dielectric needs explicit [kx, ky, kz] conductivity')
        conductivities.append([positive(x) for x in xyz])
        # Layer coordinates are copper midplanes; dielectric excludes copper.
        gap.append(positive(abs(positions[k+1]-positions[k])*1e-3-(cu[k]+cu[k+1])/2))
    convection = stack['convection_w_m2k']
    if set(convection) != {names[0], names[-1]}:
        raise ValueError('Independent convection coefficients required on both outer faces')
    ambient = p['ambient_c']
    if type(ambient) not in (int, float) or not math.isfinite(ambient) or ambient <= -273.15:
        raise ValueError('Invalid ambient temperature')
    sources = p['sources']; omissions = p['zero_power_components']
    if (set(sources) | set(omissions) != set(geo['parts']) or set(sources) & set(omissions)
            or any(not isinstance(reason, str) or not reason.strip() for reason in omissions.values())):
        raise ValueError('Every component needs a heat source or explicit zero-power reason')
    xx, yy, board, dx, dy = mesh(geo, p, step)
    gx=dy[:,None]/((dx[:-1]+dx[1:])/2)[None,:]
    gy=dx[None,:]/((dy[:-1]+dy[1:])/2)[:,None]
    masks = []; lateral = []; copper_masks = []; copper_indices = []; dielectric_indices = []; edges={}
    slices = stack['dielectric_slices']
    if type(slices) != int or not 1 <= slices <= 64:
        raise ValueError('dielectric_slices must be an integer in 1..64')
    for k, name in enumerate(names):
        copper = unary_union([shape for (_, layer), shape in geo['copper'].items() if layer == name])
        mask = contains_xy(copper, xx, yy) & board
        edges[len(masks)]=copper_edges(copper,xx,yy,mask)
        copper_indices.append(len(masks))
        copper_masks.append(mask); masks.append(mask); lateral.append((kc*cu[k]*gx,kc*cu[k]*gy))
        if k < len(gap):
            dielectric_indices.append([])
            for _ in range(slices):
                dielectric_indices[k].append(len(masks))
                masks.append(board); lateral.append(tuple(x*gap[k]/slices*f for x,f in zip(conductivities[k][:2],(gx,gy))))
    if not 0 < sum(int(mask.sum()) for mask in masks) <= 600000:
        raise ValueError('Empty or oversized layered thermal system')
    masks = np.array(masks); area = dy[:,None]*dx[None,:]*1e-6
    vertical = []
    for k, width in enumerate(gap):
        g = conductivities[k][2]*area/(width/(2*slices))
        for copper_layer, substrate in ((copper_indices[k], dielectric_indices[k][0]),
                                        (copper_indices[k+1], dielectric_indices[k][-1])):
            for r, c in zip(*np.where(masks[copper_layer])):
                vertical.append(((copper_layer, r, c), (substrate, r, c), g[r,c]))
        for a, b in zip(dielectric_indices[k], dielectric_indices[k][1:]):
            for r, c in zip(*np.where(board)):
                vertical.append(((a, r, c), (b, r, c), g[r,c]/2))
    # An absent internal foil is bonded dielectric, split equally between
    # adjoining materials; include that volume in its through-thickness path.
    for k in range(1, len(names)-1):
        g = area/((gap[k-1]/slices+cu[k])/(2*conductivities[k-1][2])
                  +(gap[k]/slices+cu[k])/(2*conductivities[k][2]))
        for r, c in zip(*np.where(board & ~copper_masks[k])):
            vertical.append(((dielectric_indices[k-1][-1], r, c), (dielectric_indices[k][0], r, c), g[r,c]))
    for via in geo['barrels']:
        plating = positive(stack['plating_mm'])*1e-3
        radius = positive(via['drill_mm'])*.5e-3
        first, last = names.index(via['top']), names.index(via['bottom'])
        if first >= last:
            raise ValueError('Barrel layers must follow native order')
        for k in range(first, last):
            contacts = []
            for layer in (copper_indices[k], copper_indices[k+1]):
                distance = np.where(masks[layer], (xx-via['xy'][0])**2+(yy-via['xy'][1])**2, np.inf)
                r, c = np.unravel_index(np.argmin(distance), distance.shape)
                if distance[r, c] > (via['drill_mm']/2+2*step)**2:
                    raise ValueError('Thermal barrel annulus unresolved; refine mesh')
                # Never attach a barrel to unrelated nearby copper.
                anchor = geo['copper'].get((via['net'], names[copper_indices.index(layer)]), Point().buffer(0))
                if not anchor.covers(Point(xx[r, c], yy[r, c])):
                    raise ValueError('Thermal barrel contact does not resolve its native annulus')
                contacts.append((layer, r, c))
            length = abs(positions[k+1]-positions[k])*1e-3
            g = kc*math.pi*((radius+plating)**2-radius**2)/length
            vertical.append((*contacts, g))
    matrix, ids = system(masks, lateral, vertical, edge_masks=edges)
    sinks = np.zeros(int(masks.sum()))
    for k, di in ((0, 0), (len(names)-1, len(gap)-1)):
        h = positive(convection[names[k]])
        sinks[ids[copper_indices[k]][copper_masks[k]]] += h*area[copper_masks[k]]
        bare = board & ~copper_masks[k]
        surface = dielectric_indices[di][0 if k == 0 else -1]
        sinks[ids[surface][bare]] += area[bare]/(1/h+gap[di]/(2*slices*conductivities[di][2]))
    matrix.setdiag(matrix.diagonal()+sinks)
    rhs = np.zeros(len(sinks)); power = 0.; contacts = {}
    for ref, source in sources.items():
        if set(source) != {'watts', 'pad', 'layer'} or source['pad'].split(':')[0] != ref:
            raise ValueError('Layered heat source requires watts and an actual pad/layer of its component')
        watts = positive(source['watts']); pad = geo['pads'][source['pad']]; name = source['layer']
        k = names.index(name)
        if name not in pad['layers']:
            raise ValueError('Heat-source pad absent on declared layer')
        hit = contains_xy(pad['layers'][name], xx, yy) & copper_masks[k]
        if not np.any(hit):
            raise ValueError('Heat-source contact unresolved; refine mesh')
        indices = ids[copper_indices[k]][hit]; weights=area[hit]/area[hit].sum()
        rhs[indices] += watts*weights
        contacts[ref] = (indices,weights); power += watts
    if not power:
        raise ValueError('Nonzero explicit heat load required')
    rise, residual, error = solve(matrix, rhs)
    rejected = float(np.sum(rise*sinks)); balance = abs(rejected-power)/power
    if balance > 1e-6 or float(np.min(rise)) < -1e-6:
        raise ValueError('Thermal energy balance or nonnegative rise failed')
    values = dict(max_board_temperature_c=ambient+float(np.max(rise)), rise_max_c=float(np.max(rise)),
                  rejected_power_w=rejected, power_balance_rel=balance, nodes=len(rise), residual_w=error)
    values['smallest_xy_cell_mm']=float(min(dx.min(),dy.min()))
    values['largest_xy_cell_mm']=float(max(dx.max(),dy.max()))
    for ref, (indices,weights) in contacts.items():
        values[ref+'_contact_max_c'] = ambient+float(np.max(rise[indices]))
        values[ref+'_contact_mean_c'] = ambient+float(np.sum(rise[indices]*weights))
    return values
