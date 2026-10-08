"""Public KiCad adapter. No PCB-bench actor or private resources."""
import json,sys
from pathlib import Path
import wx
app = wx.App(False)
import pcbnew

def readback(board):
    box=board.GetBoardEdgesBoundingBox()
    mm=pcbnew.ToMM
    points=[]
    for edge in board.GetDrawings():
        if edge.GetLayer()==pcbnew.Edge_Cuts:
            points.append({'shape':int(edge.GetShape()),'start':[mm(edge.GetStart().x),mm(edge.GetStart().y)],'end':[mm(edge.GetEnd().x),mm(edge.GetEnd().y)]})
    xs=[p[0] for e in points for p in (e['start'],e['end'])];ys=[p[1] for e in points for p in (e['start'],e['end'])]
    bounds=[min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)] if xs else []
    return {'schemaVersion':1,'version':pcbnew.Version(),'nativeBounds':[mm(box.GetX()),mm(box.GetY()),mm(box.GetWidth()),mm(box.GetHeight())],'bounds':bounds, 'edges':points,'footprints':[{'reference':f.GetReference(),'position':[mm(f.GetPosition().x),mm(f.GetPosition().y)],'rotation':f.GetOrientationDegrees()} for f in board.GetFootprints()]}

def rectangle(board,spec):
    edges=[e for e in board.GetDrawings() if e.GetLayer()==pcbnew.Edge_Cuts]
    if not ((len(edges)==1 and edges[0].GetShape()==pcbnew.SHAPE_T_RECT) or (len(edges)==4 and all(e.GetShape()==pcbnew.SHAPE_T_SEGMENT for e in edges))):
        raise ValueError('First release supports a single rectangular outline (one rectangle or four segments).')
    current=readback(board)['bounds'];x,y,w,h=current
    if w<=0 or h<=0:raise ValueError('Degenerate outline.')
    corners=[(round(x,5),round(y,5)),(round(x+w,5),round(y,5)),(round(x+w,5),round(y+h,5)),(round(x,5),round(y+h,5))]
    if len(edges)==4:
        actual=[frozenset(tuple(round(p,5) for p in end) for end in (e['start'],e['end'])) for e in readback(board)['edges']]
        expected={frozenset((corners[i],corners[(i+1)%4])) for i in range(4)}
        if len(set(actual))!=4 or set(actual)!=expected:raise ValueError('Board outline must form one complete rectangle.')
    for edge in edges:board.Remove(edge)
    x,y=spec['origin'];w,h=spec['size'];points=[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
    for i,start in enumerate(points):
        edge=pcbnew.PCB_SHAPE();edge.SetShape(pcbnew.SHAPE_T_SEGMENT);edge.SetLayer(pcbnew.Edge_Cuts);edge.SetWidth(pcbnew.FromMM(0.05));edge.SetStart(pcbnew.VECTOR2I(*(pcbnew.FromMM(n) for n in start)));edge.SetEnd(pcbnew.VECTOR2I(*(pcbnew.FromMM(n) for n in points[(i+1)%4])));board.Add(edge)

def main():
    if pcbnew.Version() != '10.0.6':raise ValueError('This profile requires KiCad 10.0.6.')
    operation,source,output=sys.argv[1:4]
    board=pcbnew.LoadBoard(source)
    if board is None:raise ValueError('KiCad failed to load board.')
    if operation=='edit':
        request=json.loads(Path(sys.argv[4]).read_text())
        if 'rectangle' in request:rectangle(board,request['rectangle'])
        for move in request.get('moves',[]):
            candidates=[f for f in board.GetFootprints() if f.GetReference()==move['reference']]
            if len(candidates)!=1:raise ValueError('Expected one footprint: '+move['reference'])
            f=candidates[0];f.SetPosition(pcbnew.VECTOR2I(*(pcbnew.FromMM(n) for n in move['position'])))
            if 'rotation' in move:f.SetOrientationDegrees(move['rotation'])
        if not pcbnew.SaveBoard(output,board):raise ValueError('Board save failed.')
    elif operation=='read':Path(output).write_text(json.dumps(readback(board),indent=2))
    else:raise ValueError('Unknown native operation.')
if __name__=='__main__':main()
