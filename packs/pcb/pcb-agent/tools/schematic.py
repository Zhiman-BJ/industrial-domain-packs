"""Generate a flat, electrically connected schematic including multi-unit symbols.

Labels attach at pin anchors. Correctness is established by exporting the
KiCad netlist and comparing pin membership, not by assuming labels connected.
"""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import uuid
from tools.sexpr import parse, dump, child, children, Quoted as Q

from tools import library
SYMBOL_DIRS = library.symbol_dirs()

def _uid(): return str(uuid.uuid4())
def _q(s): return json.dumps(str(s), ensure_ascii=False)


def resolved_symbol(lib, name, dirs=None):
    """Resolve local inheritance and retain all units; reject cycles/missing bases."""
    nodes = None
    for directory in (dirs if dirs is not None else SYMBOL_DIRS):
        path = Path(directory) / f'{lib}.kicad_sym'
        if not path.is_file(): continue
        candidates = {str(n[1]): n for n in children(parse(path.read_text()), 'symbol')}
        if name in candidates:
            nodes = candidates; break
    if nodes is None: raise LookupError(f'Symbol {lib}:{name} not found in configured libraries')
    def resolve(key, seen):
        if key in seen: raise ValueError(f'Cyclic symbol inheritance: {key}')
        if key not in nodes: raise LookupError(f'Symbol {lib}:{key} not found')
        node = copy.deepcopy(nodes[key]); ext = child(node, 'extends')
        if not ext: return node
        base = resolve(str(ext[1]), seen | {key})
        inherited = []
        overrides = {(str(n[0]), str(n[1]) if n[0] == 'property' else '') for n in node[2:] if isinstance(n, list) and n[0] not in ('extends', 'symbol')}
        for n in base[2:]:
            if isinstance(n, list):
                if n[0] == 'symbol':
                    n[1] = Q(re.sub(r'^.*(?=_\d+_\d+$)', key, str(n[1])))
                elif (str(n[0]), str(n[1]) if n[0] == 'property' else '') in overrides:
                    continue
            inherited.append(n)
        return ['symbol', Q(key)] + inherited + [n for n in node[2:] if not (isinstance(n, list) and n[0] == 'extends')]
    return resolve(name, set())


def symbol_units(node):
    """unit -> pin facts, selecting normal body style (0/1), not alternate shapes."""
    groups = {}
    for body in children(node, 'symbol'):
        m = re.search(r'_(\d+)_(\d+)$', str(body[1]))
        if not m: continue
        unit, style = map(int, m.groups())
        if style not in (0, 1): continue
        group = groups.setdefault(unit, [])
        for p in children(body, 'pin'):
            at, nm, number = child(p, 'at'), child(p, 'name'), child(p, 'number')
            if not all((at, nm, number)): raise ValueError('Incomplete pin definition')
            group.append((str(number[1]), str(nm[1]), float(at[1]), -float(at[2]), str(p[1])))
    units = {u: pins + groups.get(0, []) for u, pins in groups.items() if u > 0}
    return units or {1: groups.get(0, [])}


def _extract_symbol(lib, name):
    node = resolved_symbol(lib, name); units = symbol_units(node)
    node[1] = Q(f'{lib}:{name}')
    return dump(node), [p[:4] for u in units.values() for p in u]


@dataclass
class Part:
    ref: str
    lib_id: str
    footprint: str
    value: str
    nets: dict
    no_connect: list = field(default_factory=list)
    units: dict = field(default_factory=dict)
    pins: list = field(default_factory=list)
    schematic_only: bool = False
    pad_map: dict = field(default_factory=dict)
    placement: dict = field(default_factory=dict)


class Schematic:
    def __init__(self, name, page='A4'):
        self.name, self.page, self.uuid = name, page, _uid()
        self.parts, self._bodies = [], {}

    def _layout_positions(self):
        """Return readable, page-centred symbol anchors.

        The old renderer used ``25.4 + index * 50.8`` which made every
        schematic a five-column pile in the upper-left corner.  Placement is
        derived from the declared connectivity and the actual number of
        symbols instead.  Connected symbols are kept in the same flow order;
        disconnected groups are placed after them.  The resulting grid is a
        starting pose only, but it is centred and leaves room for labels and
        wire stubs on every side of an A3 sheet.
        """
        if not self.parts:
            return {}
        # Build a component graph from shared intent nets.  This is topology
        # information, not a task-specific reference design.
        by_net = {}
        for part in self.parts:
            for net in set(part.nets.values()):
                by_net.setdefault(net, []).append(part.ref)
        graph = {p.ref: set() for p in self.parts}
        for refs in by_net.values():
            for ref in refs:
                graph[ref].update(x for x in refs if x != ref)

        # Stable graph traversal gives a signal-flow-like order while keeping
        # isolated symbols deterministic.  Prefer endpoints, then references.
        def key(ref):
            degree = len(graph[ref])
            prefix = re.match(r'[A-Za-z]+', ref)
            rank = {'J': 0, 'P': 0, 'U': 1, 'Q': 2, 'D': 3,
                    'L': 4, 'R': 5, 'C': 6}.get((prefix.group(0) if prefix else '').upper(), 7)
            return (0 if degree <= 1 else 1, rank, ref)

        remaining = set(graph)
        order = []
        while remaining:
            start = min(remaining, key=key)
            queue = [start]; remaining.remove(start)
            while queue:
                current = queue.pop(0)
                order.append(current)
                neighbours = sorted((x for x in graph[current] if x in remaining), key=key)
                for nxt in neighbours:
                    remaining.remove(nxt)
                    queue.append(nxt)

        # Fit a compact, centred grid.  55.88/45.72 mm leaves comfortable
        # separation for standard symbols and their 2.54 mm label stubs.
        count = len(order)
        cols = max(1, min(6, int(math.ceil(math.sqrt(count * 1.65)))))
        rows = int(math.ceil(count / cols))
        step_x, step_y = 55.88, 45.72
        page_w, page_h = {'A4':(297.,210.),'A3':(420.,297.),'A2':(594.,420.),'A1':(841.,594.),'A0':(1189.,841.)}[self.page]
        # Keep the complete grid centred, with a minimum margin for title
        # blocks and page-edge labels.
        grid_w, grid_h = (cols - 1) * step_x, (rows - 1) * step_y
        cx, cy = page_w / 2.0, page_h / 2.0
        x0, y0 = cx - grid_w / 2.0, cy - grid_h / 2.0
        return {ref: (x0 + (i % cols) * step_x,
                      y0 + (i // cols) * step_y)
                for i, ref in enumerate(order)}

    @staticmethod
    def _reference_net(name):
        """Conservative net-role hint used only for drawing arrangement."""
        token = re.sub(r'[^A-Za-z0-9]+', '', str(name)).upper()
        return bool(re.fullmatch(r'(?:GND|AGND|DGND|PGND|CHASSIS|SHIELD|EARTH|VCC|VDD|VSS|VEE|VIN|VBUS|V[A-Z]?IN|[+]?\d+V\d*)', token))

    def _topology_layout(self):
        """Place a main signal path and its branches in a centred page pose."""
        positions = self._layout_positions()
        if not self.parts:
            return positions
        refs = [p.ref for p in self.parts]
        # Nets used only as supplies/returns are drawn as buses. The remaining
        # graph captures signal flow and avoids letting a common GND net make a
        # completely unrelated set of parts look like one chain.
        by_net = {}
        for p in self.parts:
            for net in set(p.nets.values()):
                if not self._reference_net(net):
                    by_net.setdefault(net, []).append(p.ref)
        graph = {r: set() for r in refs}
        for connected in by_net.values():
            for r in connected:
                graph[r].update(x for x in connected if x != r)

        def stable(ref):
            return (len(graph[ref]), ref)

        def farthest(start):
            dist = {start: 0}; parent = {start: None}; queue = [start]
            while queue:
                cur = queue.pop(0)
                for nxt in sorted(graph[cur], key=stable):
                    if nxt not in dist:
                        dist[nxt] = dist[cur] + 1; parent[nxt] = cur; queue.append(nxt)
            end = max(dist, key=lambda r: (dist[r], r))
            path = []
            while end is not None:
                path.append(end); end = parent[end]
            return path[::-1]

        start = min(refs, key=stable)
        backbone = farthest(start)
        if backbone:
            backbone = farthest(backbone[-1])
        if not backbone:
            backbone = [refs[0]]

        # When interface names carry a directional hint, put an input side on
        # the left and an output side on the right. Otherwise retain the
        # deterministic graph order above.
        def flow_rank(ref):
            nets = ' '.join(str(n) for p in self.parts if p.ref == ref for n in p.nets.values()).upper()
            if re.search(r'(^|[^A-Z0-9])(IN|INPUT|RX|SCLK|SDA|MOSI)([^A-Z0-9]|$)', nets): return 0
            if re.search(r'(^|[^A-Z0-9])(OUT|OUTPUT|TX|MISO|SCL)([^A-Z0-9]|$)', nets): return 2
            return 1
        if len(backbone) > 1 and flow_rank(backbone[0]) > flow_rank(backbone[-1]):
            backbone.reverse()

        page_w, page_h = {'A4':(297.,210.),'A3':(420.,297.),'A2':(594.,420.),'A1':(841.,594.),'A0':(1189.,841.)}[self.page]
        cy = page_h / 2.0 - 8.0
        step_x = min(38.1, (page_w - 80.0) / max(1, len(backbone) - 1))
        x0 = page_w / 2.0 - step_x * (len(backbone) - 1) / 2.0
        placed = set()
        for i, ref in enumerate(backbone):
            positions[ref] = (x0 + i * step_x, cy); placed.add(ref)

        # Branches are placed below/above their nearest backbone neighbour;
        # later rows prevent a large fan-out from collapsing onto one point.
        branch_index = 0
        for ref in refs:
            if ref in placed:
                continue
            neighbours = [x for x in graph[ref] if x in placed]
            part = next(p for p in self.parts if p.ref == ref)
            has_ground = any(re.search(r'GND|GROUND|AGND|DGND|PGND|CHASSIS|SHIELD|EARTH', str(n), re.I)
                             for n in part.nets.values())
            if neighbours:
                anchor = sum(positions[x][0] for x in neighbours) / len(neighbours)
                direction = 1 if has_ground else (-1 if branch_index % 2 else 1)
                row = branch_index // 2
                positions[ref] = (anchor, cy + direction * (17.78 + row * 25.4))
            else:
                col = branch_index % 4
                row = branch_index // 4
                positions[ref] = (page_w / 2.0 + (col - 1.5) * 62.0,
                                  cy + (1 if row % 2 == 0 else -1) * (78.0 + row * 25.0))
            placed.add(ref); branch_index += 1

        # Re-centre and keep every anchor inside a useful drawing margin. The
        # wire renderer adds its own edge clearance around these anchors.
        xs = [positions[r][0] for r in refs]; ys = [positions[r][1] for r in refs]
        dx = page_w / 2.0 - (min(xs) + max(xs)) / 2.0
        dy = page_h / 2.0 - (min(ys) + max(ys)) / 2.0
        centred = {r: (self._snap(min(page_w - 25.0, max(25.0, positions[r][0] + dx))),
                       self._snap(min(page_h - 25.0, max(25.0, positions[r][1] + dy)))) for r in refs}
        return self._separate_symbols(centred, page_w, page_h)

    def _separate_symbols(self, positions, page_w, page_h):
        """Reserve pin, stub and unit space before wiring any symbol.

        Topological neighbours can suggest identical anchors (for example two
        shunt branches). Distinct-net pins must never become native connections
        merely because their drawing poses coincide.
        """
        occupied=[];placed={}
        grid=[(self._snap(x*5.08),self._snap(y*5.08))
              for x in range(3,int((page_w-10)/5.08))
              for y in range(3,int((page_h-10)/5.08))]
        for part in self.parts:
            rotation=self._orientation(part)
            points=[(0.,0.)]
            for unit,pins in part.units.items():
                for _,_,x,y,_ in pins:
                    px,py,_=self._transform(x,y,0,rotation,(0,(unit-1)*25.4))
                    # Connector mirroring happens after layout; reserve both
                    # orientations so it cannot invalidate this separation.
                    points.extend(((px,py),(-px,py)))
            margin=5.08
            extent=(min(x for x,y in points)-margin,min(y for x,y in points)-margin,
                    max(x for x,y in points)+margin,max(y for x,y in points)+margin)
            desired=positions[part.ref]
            candidates=[desired,*sorted(grid,key=lambda p:(p[0]-desired[0])**2+(p[1]-desired[1])**2)]
            for x,y in candidates:
                box=(x+extent[0],y+extent[1],x+extent[2],y+extent[3])
                if box[0]<10 or box[1]<10 or box[2]>page_w-10 or box[3]>page_h-10:continue
                if any(box[0]<b[2] and b[0]<box[2] and box[1]<b[3] and b[1]<box[3] for b in occupied):continue
                occupied.append(box);placed[part.ref]=(x,y);break
            else:raise ValueError('Schematic page cannot fit separate symbol/pin regions; use a larger page or hierarchical sheets')
        if not placed:return placed
        # Re-centre while keeping every reserved region inside the page.
        xs,ys=zip(*placed.values())
        dx=max(10-min(b[0] for b in occupied),min(page_w-10-max(b[2] for b in occupied),page_w/2-(min(xs)+max(xs))/2))
        dy=max(10-min(b[1] for b in occupied),min(page_h-10-max(b[3] for b in occupied),page_h/2-(min(ys)+max(ys))/2))
        return {r:(self._snap(x+dx),self._snap(y+dy)) for r,(x,y) in placed.items()}

    @staticmethod
    def _orientation(part):
        """Choose a readable pose from symbol class and declared net roles."""
        if len(part.pins) != 2:
            return 0.0
        name = part.lib_id.rsplit(':', 1)[-1].upper()
        nets = list(part.nets.values())
        # Series passives read left-to-right. Shunt capacitors and parts tied
        # to a return remain vertical so their ground wire naturally drops to a
        # return bus. This is a generic visual convention, never a netlist rule.
        if name in {'R', 'RESISTOR', 'L', 'INDUCTOR', 'FB', 'FERRITE_BEAD'} and not any(Schematic._reference_net(n) for n in nets):
            # KiCad's positive schematic rotation is clockwise in its file
            # convention; 90° places pin 1 on the left and pin 2 on the right.
            return 90.0
        return 0.0

    def _connector_mirrors(self, positions):
        """Face single-sided connectors toward their connected signal parts."""
        mirrors = set()
        for part in self.parts:
            if not part.lib_id.startswith(('Connector:', 'Connector_Generic:')) or not part.pins:
                continue
            xs = [pin[2] for pin in part.pins]
            side = -1 if all(x < 0 for x in xs) else 1 if all(x > 0 for x in xs) else 0
            nets = {n for n in part.nets.values() if not self._reference_net(n)}
            neighbours = [p for p in self.parts if p.ref != part.ref and nets.intersection(p.nets.values())]
            if side and neighbours:
                desired = sum(positions[p.ref][0] for p in neighbours) / len(neighbours) - positions[part.ref][0]
                if desired * side < 0:
                    mirrors.add(part.ref)
        return mirrors

    @staticmethod
    def _snap(value, grid=1.27):
        return round(float(value) / grid) * grid

    @staticmethod
    def _transform(x, y, angle, rotation, origin, snap=True):
        # S-expression symbol poses use a clockwise screen rotation, while
        # the ordinary Cartesian transform below is counter-clockwise.
        theta = math.radians(-rotation)
        tx = math.cos(theta) * x - math.sin(theta) * y
        ty = math.sin(theta) * x + math.cos(theta) * y
        coordinate=Schematic._snap if snap else lambda v:round(v,4)
        return coordinate(origin[0] + tx), coordinate(origin[1] + ty), angle + rotation

    @staticmethod
    def _wire(a, b):
        a = tuple(round(float(v),4) for v in a)
        b = tuple(round(float(v),4) for v in b)
        if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
            return None
        return f'(wire (pts (xy {a[0]:.4f} {a[1]:.4f}) (xy {b[0]:.4f} {b[1]:.4f})) (stroke (width 0) (type default)) (uuid "{_uid()}"))'

    @staticmethod
    def _junction(x, y):
        return f'(junction (at {x:.4f} {y:.4f}) (diameter 0) (color 0 0 0 0) (uuid "{_uid()}"))'

    def _wiring(self, records, boxes):
        """Draw safe orthogonal connections, with labels for separated groups.

        Wire geometry must preserve the pin graph. Never run a signal through
        another net's stub or through a symbol to make the drawing shorter.
        Named connections are a normal schematic convention when a clear
        geometric path is unavailable; they retain each actual pin endpoint.
        """
        def point(p): return tuple(round(float(v), 4) for v in p)
        def on(p, a, b):
            return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
                    min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))
        def intersects(a, b, c, d):
            return (max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) and
                    max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1])))
        def body_hit(a, b, box):
            x0, y0, x1, y1 = box
            if a[1] == b[1]:
                return y0 < a[1] < y1 and max(min(a[0], b[0]), x0) < min(max(a[0], b[0]), x1)
            return x0 < a[0] < x1 and max(min(a[1], b[1]), y0) < min(max(a[1], b[1]), y1)

        by_net, wires, labels, label_anchors = {}, [], [], {}
        pins = [(point(r[3]), r[6]) for r in records]
        for r in records:
            if r[6]:
                by_net.setdefault(r[6], []).append(r)
                a, b = point(r[3]), point(r[4])
                if a != b: wires.append((r[6], a, b))
        page_w, page_h = {'A4':(297.,210.),'A3':(420.,297.),'A2':(594.,420.),'A1':(841.,594.),'A0':(1189.,841.)}[self.page]

        def safe(net, segments):
            for a, b in segments:
                if any(not 10 <= p[0] <= page_w - 10 or not 10 <= p[1] <= page_h - 10 for p in (a, b)):
                    return False
                if any(body_hit(a, b, box) for box in boxes): return False
                if any(other != net and on(p, a, b) for p, other in pins): return False
                if any(other != net and intersects(a, b, c, d) for other, c, d in wires): return False
            return True

        def path(net, a, b):
            candidates = [[a, (b[0], a[1]), b], [a, (a[0], b[1]), b]]
            # Search a bounded set of clear drawing lanes. A failed search
            # produces a labelled connection, never an unchecked wire.
            for step in range(1, 17):
                for sign in (-1, 1):
                    y = point((0, (min(a[1], b[1]) if sign < 0 else max(a[1], b[1])) + sign * step * 5.08))[1]
                    x = point(((min(a[0], b[0]) if sign < 0 else max(a[0], b[0])) + sign * step * 5.08, 0))[0]
                    candidates.extend(([a, (a[0], y), (b[0], y), b], [a, (x, a[1]), (x, b[1]), b]))
            candidates.sort(key=lambda ps: sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for a,b in zip(ps, ps[1:])))
            for ps in candidates:
                segments = [(a, b) for a, b in zip(ps, ps[1:]) if a != b]
                if safe(net, segments): return segments
            return None

        for net, group in sorted(by_net.items()):
            ends = [point(r[4]) for r in group]
            components = [[0]]
            for i in range(1, len(ends)):
                joined = False
                if not self._reference_net(net):
                    for component in components:
                        for j in sorted(component, key=lambda j: abs(ends[j][0]-ends[i][0])+abs(ends[j][1]-ends[i][1])):
                            segments = path(net, ends[j], ends[i])
                            if segments is not None:
                                wires.extend((net, a, b) for a, b in segments)
                                component.append(i); joined = True; break
                        if joined: break
                if not joined: components.append([i])
            for component in components:
                r = group[component[0]]; at = point(r[4]); angle = (r[5] + 180) % 360
                justify = 'right' if angle in (180, 270) else 'left'
                labels.append(f'(global_label {_q(net)} (shape bidirectional) (at {at[0]:.4f} {at[1]:.4f} {angle:g}) (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{_uid()}"))')
                label_anchors.setdefault(net,set()).add(at)

        objects = []
        for net in by_net:
            segments = [(a, b) for n, a, b in wires if n == net]
            taps = {p for edge in segments for p in edge}
            # Split at every same-net endpoint/intersection, including a pin
            # stub that lands on the middle of a trunk. Without the split,
            # KiCad can export that branch as an unconnected pin.
            for a, b in segments:
                for c, d in segments:
                    if a[1] == b[1] and c[0] == d[0] and intersects(a, b, c, d): taps.add((c[0], a[1]))
            edges = set()
            for a, b in segments:
                pts = sorted(p for p in taps if on(p, a, b))
                edges.update((start, end) for start, end in zip(pts, pts[1:]) if start != end)
            # Overlapping routes can leave an unused stub past a junction.
            # Prune only generated leaf edges with no pin or label at the end;
            # every intended electrical terminal remains protected.
            protected={p for p,n in pins if n==net}|label_anchors.get(net,set())
            while edges:
                counts={}
                for edge in edges:
                    for p in edge:counts[p]=counts.get(p,0)+1
                leaves={p for p,n in counts.items() if n==1 and p not in protected}
                if not leaves:break
                edges={edge for edge in edges if not leaves.intersection(edge)}
            degree = {}
            for a, b in sorted(edges):
                objects.append(self._wire(a, b))
                for p in (a, b): degree[p] = degree.get(p, 0) + 1
            objects.extend(self._junction(*p) for p, n in sorted(degree.items()) if n > 2)
        return objects + labels

    def add_part(self, ref, lib_id, footprint='', nets=None, value=None, no_connect=None, schematic_only=False, pad_map=None, placement=None):
        if any(p.ref == ref for p in self.parts): raise ValueError(f'Duplicate reference {ref}')
        lib, name = lib_id.split(':', 1)
        node = resolved_symbol(lib, name)
        # Renumber embedded pins to their explicit physical pad mapping so the
        # official schematic parity check sees exactly the same identifiers.
        mapping = pad_map or {}
        for body in children(node, 'symbol'):
            for pin in children(body, 'pin'):
                num = child(pin, 'number')
                if num and str(num[1]) in mapping: num[1] = Q(str(mapping[str(num[1])]))
        units = symbol_units(node)
        pins = [p[:4] for u in units.values() for p in u]
        original_pins = {p[0] for u in symbol_units(resolved_symbol(lib, name)).values() for p in u}
        unknown = (set(nets or {}) | set(no_connect or [])) - original_pins
        if unknown: raise ValueError(f'{ref}: unknown pins {sorted(unknown)}')
        if set(nets or {}) & set(no_connect or []): raise ValueError('Connected pin marked NC')
        mapped = {str(mapping.get(k, k)): v for k, v in (nets or {}).items()}
        nc = [str(mapping.get(k, k)) for k in (no_connect or [])]
        # Mapping changes embedded symbol identity; avoid sharing with unmapped instances.
        embedded = lib_id if not mapping else f'{lib_id}__{ref}'
        node[1] = Q(embedded); self._bodies[embedded] = dump(node)
        p = Part(ref, embedded, footprint, str(name if value is None else value), mapped, nc, units, pins, schematic_only, mapping, placement or {})
        self.parts.append(p)
        return p

    def render(self):
        objects, idx = [], 0
        pages=('A4','A3','A2','A1','A0')
        if self.page not in pages:raise ValueError('Supported drawing pages: '+', '.join(pages))
        for page in pages[pages.index(self.page):]:
            self.page=page
            try:
                positions=self._topology_layout();break
            except ValueError as error:
                if 'Schematic page cannot fit' not in str(error) or page=='A0':raise
        orientations = {p.ref: self._orientation(p) for p in self.parts}
        mirrors = self._connector_mirrors(positions)
        # Records contain actual transformed pin anchors so subsequent wires
        # cannot silently miss a pin after an orientation change.
        pin_records = []
        symbol_boxes = []
        for p in self.parts:
            body = parse(self._bodies[p.lib_id])
            for unit, pins in sorted(p.units.items()):
                base_x, base_y = positions[p.ref]
                x, y = base_x, base_y + (unit - 1) * 25.4
                rotation = orientations[p.ref]
                pose=p.placement.get(str(unit),{})
                if pose:
                    x,y=pose['at'];rotation=pose['rotation']
                mirror_axis=pose.get('mirror','y' if p.ref in mirrors else '')
                idx += 1
                anchors = [(x, y)]
                for num, nm, dx, dy, ty in pins:
                    raw_angle = next((float(child(pin, 'at')[3])
                                      for b in children(body, 'symbol')
                                      for pin in children(b, 'pin')
                                      if str(child(pin, 'number')[1]) == num), 0.0)
                    if not pose and mirror_axis=='y':
                        dx = -dx
                        raw_angle = (180 - raw_angle) % 360
                    elif not pose and mirror_axis=='x':
                        dy = -dy
                        raw_angle = -raw_angle % 360
                    ax, ay, outward_angle = self._transform(dx, dy, raw_angle, rotation, (x, y),snap=not bool(pose))
                    if pose and mirror_axis=='y':ax=2*x-ax;outward_angle=(180-outward_angle)%360
                    elif pose and mirror_axis=='x':ay=2*y-ay;outward_angle=-outward_angle%360
                    anchors.append((ax, ay))
                    vector = (-math.cos(math.radians(outward_angle)),
                              math.sin(math.radians(outward_angle)))
                    stub = (ax + 2.54 * vector[0], ay + 2.54 * vector[1])
                    pin_records.append((p, unit, num, (ax, ay), stub,
                                        outward_angle, p.nets.get(num)))
                    if num in p.no_connect:
                        objects.append(f'(no_connect (at {ax:.4f} {ay:.4f}) (uuid "{_uid()}"))')
                xs, ys = zip(*anchors)
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                if x1 - x0 < 2.54: x0 -= 1.27; x1 += 1.27
                if y1 - y0 < 2.54: y0 -= 1.27; y1 += 1.27
                if not p.schematic_only: symbol_boxes.append((x0, y0, x1, y1))
                properties=[]
                for key,value,fallback in [('Reference',p.ref,-2.54),('Value',p.value,2.54),('Footprint',p.footprint,5.08)]:
                    preserved=pose.get('fields',{}).get(key)
                    if preserved:
                        prop=copy.deepcopy(preserved);prop[2]=Q(value);properties.append(dump(prop));continue
                    template=next((n for n in children(body,'property') if str(n[1])==key),None)
                    if template is not None and child(template,'at'):
                        prop=copy.deepcopy(template);prop[2]=Q(value);at=child(prop,'at')
                        local_angle=float(at[3]) if len(at) > 3 else 0.0
                        px,py,pangle=self._transform(float(at[1]),-float(at[2]),local_angle,rotation,(x,y))
                        # Keep reference/value text horizontal after rotating a
                        # passive. Put the value below the body so it never
                        # collides with the reference or pin numbers.
                        if key in ('Reference', 'Value'):
                            # Field angles are relative to the symbol pose in
                            # KiCad; cancel the pose to obtain an upright
                            # screen orientation.
                            pangle = -rotation
                            if key == 'Value' and rotation in (90.0, 270.0) and len(pins) == 2:
                                py = self._snap(y + 5.08)
                        at[1:4]=[px,py,pangle]
                        if key=='Footprint':
                            effects=child(prop,'effects')
                            if effects is not None and not child(effects,'hide'):effects.append(['hide','yes'])
                        properties.append(dump(prop))
                    else:
                        properties.append(f'(property {_q(key)} {_q(value)} (at {x:.4f} {y+fallback:.4f} {rotation:g}) (effects (font (size 1.27 1.27)){" (hide yes)" if key == "Footprint" else ""}))')
                props=''.join(properties)
                pin_ids = ''.join(f'(pin {_q(n)} (uuid "{_uid()}"))' for n, *_ in pins)
                on = 'no' if p.schematic_only else 'yes'
                mirror = f'(mirror {mirror_axis})' if mirror_axis else ''
                objects.append(f'(symbol (lib_id {_q(p.lib_id)}) (at {x:.4f} {y:.4f} {rotation:g}) {mirror} (unit {unit}) (in_bom {on}) (on_board {on}) (dnp no) (uuid "{_uid()}") {props}{pin_ids} (instances (project {_q(self.name)} (path "/{self.uuid}" (reference {_q(p.ref)}) (unit {unit})))))')

        objects.extend(self._wiring(pin_records, symbol_boxes))

        paper = f'(paper {_q(self.page)})'
        return f'(kicad_sch (version 20231120) (generator "pcb-agent") (uuid "{self.uuid}") {paper} (lib_symbols {" ".join(self._bodies.values())}) {" ".join(objects)} (sheet_instances (path "/" (page "1"))))\n'

    def write(self, path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render()); return str(path)


def write_project(path, name):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    project=json.loads(path.read_text()) if path.is_file() else {'meta': {'filename': f'{name}.kicad_pro', 'version': 3}, 'board': {'design_settings': {'defaults': {}}}, 'schematic': {}, 'sheets': []}
    if not isinstance(project,dict):raise ValueError('Existing KiCad project must be an object')
    path.write_text(json.dumps(project,indent=2))
    return str(path)


def generate(spec: dict, out_dir: str, name: str = 'board') -> dict:
    """Generate native schematic/project and library tables, without a PCB; does not verify."""
    from tools import bootstrap
    return bootstrap.build(out_dir, name, spec, schematic_only=True)
