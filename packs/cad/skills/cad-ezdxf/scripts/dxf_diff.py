"""dxf_diff —— 语义级 DXF diff（任务 08 核心可复用资产，MCP-C）

读两个 DXF，输出实体清单级差异：
  - CIRCLE   按 (图层 / 圆心 / 直径) 聚类
  - LINE     按 (图层 / 端点集合 / 线型 / 线宽) 聚类
  - TEXT     按 (内容 / 位置) 聚类
  - DIMENSION 按 (测量值 / 显示文本) 聚类
容差 TOL=1e-3（坐标/尺寸/半径/字高/圆心距离均按此判等）。

每条差异给出**建议分类**（启发式，供人复核，非定论）：
  intent     = 意图级（设计决策：零件尺寸/孔位/标注测量值/设计文字/轮廓几何）
  appearance = 外观级（呈现：中心线、图框、标签微移、标注摆放、线型线宽）
  ambiguous  = 有歧义（如单个孔偏移：设计意图 vs 误碰）→ 报告标注"此处应询问用户"

聚类思路：
  1) 按 key 对 A/B 实体计数，common = 交集（两边都有、完全相同）。
  2) removed = A 中多出的（不在 common），added = B 中多出的。
  3) 在 removed × added 内做"同层 + 同直径/同内容/同测量值"的贪心配对，
     识别"改了某个属性"而非"删+加"。配不上的记为纯增/纯删。
  4) 线型/线宽/颜色等图层属性变化单列为 appearance。

用法:
  python3 dxf_diff.py <base.dxf> <edited.dxf> [--json out.json]
  退出码恒为 0（diff 工具，不因有差异而失败；差异数打印到 stdout）。

⚠️ 可信性声明：两个文件均用**同一读回器 ezdxf** 解析（同源读回）。
   对"ezdxf 能读到的字段"结果可信，但**不是独立交叉验证**——
   若改动落在 ezdxf 未建模的字段 / 更高层 DXF 版本差异 / 代理实体上，
   可能被漏读或误读。本报告必须保留此声明，不得声称"独立验证"。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter

TOL = 1e-3


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------
def _r(v):
    """数值圆整：|v|<TOL/2 → 0.0（吃掉浮点噪声如 2.7e-15），否则保留 3 位。"""
    v = float(v)
    return 0.0 if abs(v) < TOL / 2 else round(v, 3)


def _pt(p):
    return (_r(p[0]), _r(p[1]), _r(p[2] if len(p) > 2 else 0.0))


def _dim_display_text(dim):
    """DIMENSION 匿名几何块里的渲染显示文本（含 %%c 转义）。取不到则回退实体 text。"""
    out = []
    try:
        block = dim.get_geometry_block()
    except Exception:
        block = None
    if block is not None:
        for ge in block:
            if ge.dxftype() in ("MTEXT", "TEXT"):
                out.append(str(ge.dxf.text))
    raw = str(dim.dxf.get("text", ""))
    return "|".join(out) if out else raw


def load(path):
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ents = {"CIRCLE": [], "LINE": [], "TEXT": [], "DIMENSION": [], "OTHER": []}
    for e in msp:
        t = e.dxftype()
        if t == "CIRCLE":
            ents["CIRCLE"].append({
                "layer": e.dxf.layer, "center": _pt(e.dxf.center),
                "diameter": _r(2 * e.dxf.radius), "radius": _r(e.dxf.radius),
                "linetype": e.dxf.linetype, "lineweight": e.dxf.lineweight,
                "handle": e.dxf.handle,
            })
        elif t == "LINE":
            a, b = _pt(e.dxf.start), _pt(e.dxf.end)
            ents["LINE"].append({
                "layer": e.dxf.layer, "pts": (tuple(sorted((a, b)))),
                "start": a, "end": b,
                "linetype": e.dxf.linetype, "lineweight": e.dxf.lineweight,
                "handle": e.dxf.handle,
            })
        elif t == "TEXT":
            ents["TEXT"].append({
                "layer": e.dxf.layer, "text": str(e.dxf.text), "pos": _pt(e.dxf.insert),
                "height": _r(e.dxf.height), "style": e.dxf.style, "handle": e.dxf.handle,
            })
        elif t == "DIMENSION":
            ents["DIMENSION"].append({
                "layer": e.dxf.layer,
                "measurement": _r(e.dxf.get("actual_measurement", -1e9)),
                "display_text": _dim_display_text(e),
                "dimtype": e.dxf.get("dimtype", None), "dimstyle": e.dxf.dimstyle,
                "raw_text": str(e.dxf.get("text", "")),   # 文本模板（如 '4×<>'/'<>'）
                "handle": e.dxf.handle,
            })
        else:
            ents["OTHER"].append({"type": t, "layer": e.dxf.layer, "handle": e.dxf.handle})
    return ents, doc


def _cd(a, b):
    return math.dist(a[:2], b[:2])


# ----------------------------------------------------------------------
# key + 分类
# ----------------------------------------------------------------------
def _kc(e): return (e["layer"], e["center"], e["diameter"])
def _kl(e): return (e["layer"], e["pts"], e["linetype"], e["lineweight"])
def _kt(e): return (e["layer"], e["text"], e["pos"])
def _kd(e): return (e["layer"], e["measurement"], e["display_text"])


def _pair_by_handle(removed, added):
    """handle 相同 = 同一实体被修改（最强证据，用户手改通常保留 handle）。"""
    used = set(); mods = []
    for a in removed:
        for j, b in enumerate(added):
            if j in used or a["handle"] != b["handle"]:
                continue
            used.add(j)
            mods.append((a, b))
            break
    return mods, used


def _subset(ents, keys, cnt, common):
    """取出 ents 中不属于 common 的（按 key 计数差），保持原顺序。"""
    left = {k: cnt[k] - common.get(k, 0) for k in cnt}
    out = []
    for e, k in zip(ents, keys):
        if left.get(k, 0) > 0:
            out.append(e)
            left[k] -= 1
    return out


# ---------------- CIRCLE ----------------
def _classify_circle(m):
    k = m["kind"]
    if k == "diameter":
        return ("intent", f"直径 {m['a_dia']}→{m['b_dia']}（零件尺寸/孔径设计变更）")
    if k == "center":
        return ("intent", f"圆心 {m['a_c']}→{m['b_c']}（孔位/位置设计变更；若属误碰需人工确认）")
    if k == "both":
        return ("intent", f"圆 Ø{m['a_dia']}@{m['a_c']}→Ø{m['b_dia']}@{m['b_c']}（尺寸+位置均变）")
    if k == "added":
        return ("intent", f"新增圆 Ø{m['b_dia']} @ {m['b_c']}（新增几何）")
    if k == "removed":
        return ("intent", f"删除圆 Ø{m['a_dia']} @ {m['a_c']}（删除几何）")
    return ("ambiguous", "圆改动性质不明")


def _pair_circles(removed, added):
    # 1) handle 相同优先（同一实体被修改）
    hmods, used = _pair_by_handle(removed, added)
    mods = []
    for a, b in hmods:
        sd = abs(a["diameter"] - b["diameter"]) <= TOL
        sc = _cd(a["center"], b["center"]) <= TOL
        mods.append({"type": "CIRCLE",
                     "kind": ("center" if sd else "diameter") if (sd != sc) else "both",
                     "layer": a["layer"],
                     "a_dia": a["diameter"], "b_dia": b["diameter"],
                     "a_c": a["center"], "b_c": b["center"],
                     "a_h": a["handle"], "b_h": b["handle"]})
    # 2) 剩余：同层 + 同直径（→位置改）或 同圆心（→直径改）
    for a in removed:
        if a["handle"] in {m["a_h"] for m in mods}:
            continue
        best = None
        for j, b in enumerate(added):
            if j in used or a["layer"] != b["layer"]:
                continue
            sd = abs(a["diameter"] - b["diameter"]) <= TOL
            sc = _cd(a["center"], b["center"]) <= TOL
            if sd and sc:
                continue
            if not sd and not sc:
                continue
            mods.append({"type": "CIRCLE",
                         "kind": ("center" if sd else "diameter") if (sd != sc) else "both",
                         "layer": a["layer"],
                         "a_dia": a["diameter"], "b_dia": b["diameter"],
                         "a_c": a["center"], "b_c": b["center"],
                         "a_h": a["handle"], "b_h": b["handle"]})
            used.add(j); best = j
            break
    return mods, used


# ---------------- LINE ----------------
def _classify_line(m):
    app_layers = {"CENTER", "FRAME", "HIDDEN", "CENTERLINE", "THIN"}
    if m["layer"] in app_layers:
        return ("appearance", f"{m['layer']} 图层直线（中心线/图框，纯呈现）")
    return ("intent", f"{m['layer']} 图层直线（轮廓/零件几何变更）")


def _line_dist(pa, pb):
    best = 1e18
    for p0, p1 in ((0, 1), (1, 0)):
        d = math.dist(pa[p0][:2], pb[0][:2]) + math.dist(pa[p1][:2], pb[1][:2])
        best = min(best, d)
    return best / 2.0


def _pair_lines(removed, added):
    hmods, used = _pair_by_handle(removed, added)
    mods = []
    for a, b in hmods:
        d = _line_dist(a["pts"], b["pts"])
        mods.append({"type": "LINE", "kind": "moved", "layer": a["layer"],
                     "a_pts": a["pts"], "b_pts": b["pts"], "dist": round(d, 4),
                     "a_h": a["handle"], "b_h": b["handle"]})
    for a in removed:
        if a["handle"] in {m["a_h"] for m in mods}:
            continue
        best = None
        for j, b in enumerate(added):
            if j in used or a["layer"] != b["layer"]:
                continue
            if a["linetype"] != b["linetype"] or a["lineweight"] != b["lineweight"]:
                continue
            d = _line_dist(a["pts"], b["pts"])
            if d <= TOL:
                continue
            if d < 1.0:  # 轻微位移视为"改"，否则各记增删
                best = (j, d); break
        if best:
            j, d = best
            b = added[j]
            used.add(j)
            mods.append({"type": "LINE", "kind": "moved", "layer": a["layer"],
                         "a_pts": a["pts"], "b_pts": b["pts"], "dist": round(d, 4),
                         "a_h": a["handle"], "b_h": b["handle"]})
    return mods, used


# ---------------- TEXT ----------------
_INTENT_KW = ("法兰", "材料", "2A12", "比例", "1:1", "表面", "处理", "阳极", "氧化",
              "粗糙度", "公差", "未注", "技术要求", "零件名", "图号", "重量", "热处理")


def _text_is_intent(s):
    return any(k in s for k in _INTENT_KW)


def _classify_text(m):
    k = m["kind"]
    content = m.get("b_text") or m.get("a_text")
    if k == "pos":
        return ("appearance", f"文本 {content!r} 位置 {m['a_pos']}→{m['b_pos']}（内容不变，标签微移）")
    if k == "height":
        return ("appearance", f"文本 {content!r} 字高 {m['a_ht']}→{m['b_ht']}（呈现）")
    if k == "added":
        it = _text_is_intent(m.get("b_text", ""))
        return (("intent" if it else "ambiguous"),
                f"新增文本 {m.get('b_text')!r} @ {m.get('b_pos')}" + ("（设计/技术要求信息）" if it else "（性质待确认）"))
    if k == "removed":
        it = _text_is_intent(m.get("a_text", ""))
        return (("intent" if it else "ambiguous"),
                f"删除文本 {m.get('a_text')!r} @ {m.get('a_pos')}" + ("（设计/技术要求信息）" if it else "（性质待确认）"))
    return ("intent", f"文本内容 {m['a_text']!r}→{m['b_text']!r}（标题栏/设计信息变更）")


def _pair_texts(removed, added):
    hmods, used = _pair_by_handle(removed, added)
    mods = []
    for a, b in hmods:
        st = a["text"] == b["text"]
        sp = _cd(a["pos"], b["pos"]) <= TOL
        if st and sp:
            continue
        mods.append({"type": "TEXT",
                     "kind": ("pos" if st else ("height" if abs(a["height"]-b["height"])>TOL else "content")),
                     "layer": a["layer"],
                     "a_text": a["text"], "b_text": b["text"],
                     "a_pos": a["pos"], "b_pos": b["pos"],
                     "a_ht": a["height"], "b_ht": b["height"],
                     "a_h": a["handle"], "b_h": b["handle"]})
    for a in removed:
        if a["handle"] in {m["a_h"] for m in mods}:
            continue
        best = None
        for j, b in enumerate(added):
            if j in used or a["layer"] != b["layer"]:
                continue
            st = a["text"] == b["text"]
            sp = _cd(a["pos"], b["pos"]) <= TOL
            if st and sp:
                continue
            if not st and not sp:
                continue
            mods.append({"type": "TEXT",
                         "kind": ("pos" if st and not sp else "content"),
                         "layer": a["layer"],
                         "a_text": a["text"], "b_text": b["text"],
                         "a_pos": a["pos"], "b_pos": b["pos"],
                         "a_ht": a["height"], "b_ht": b["height"],
                         "a_h": a["handle"], "b_h": b["handle"]})
            best = j
            break
        if best is not None:
            used.add(best)
    return mods, used


# ---------------- DIMENSION ----------------
def _classify_dim(m):
    k = m["kind"]
    if k in ("measurement", "both"):
        return ("intent", f"标注测量值 {m['a_m']}→{m['b_m']}（尺寸设计变更）文本 {m['a_txt']!r}→{m['b_txt']!r}")
    if k == "text":
        return ("appearance", f"标注显示文本 {m['a_txt']!r}→{m['b_txt']!r}（测量值不变，仅文本/摆放）")
    if k == "added":
        return ("intent", f"新增标注 测量值 {m.get('b_m', m.get('measurement'))} 文本 {m.get('b_txt', m.get('display_text'))!r}")
    if k == "removed":
        return ("intent", f"删除标注 测量值 {m.get('a_m', m.get('measurement'))} 文本 {m.get('a_txt', m.get('display_text'))!r}")
    return ("ambiguous", "标注改动性质不明")


def _pair_dims(removed, added):
    hmods, used = _pair_by_handle(removed, added)
    mods = []
    for a, b in hmods:
        sm = abs(a["measurement"] - b["measurement"]) <= TOL
        st = a["display_text"] == b["display_text"]
        if sm and st:
            continue
        mods.append({"type": "DIMENSION",
                     "kind": ("both" if (not sm and not st) else ("measurement" if not sm else "text")),
                     "layer": a["layer"],
                     "a_m": a["measurement"], "b_m": b["measurement"],
                     "a_txt": a["display_text"], "b_txt": b["display_text"],
                     "a_h": a["handle"], "b_h": b["handle"]})
    # 剩余：同层 + 同 dimtype + 同文本模板（<> 类）→ 视为同一标注的测量值变化
    for a in removed:
        if a["handle"] in {m["a_h"] for m in mods}:
            continue
        best = None
        for j, b in enumerate(added):
            if j in used or a["layer"] != b["layer"]:
                continue
            sm = abs(a["measurement"] - b["measurement"]) <= TOL
            st = a["display_text"] == b["display_text"]
            if sm and st:
                continue
            same_tpl = a.get("raw_text", "") == b.get("raw_text", "") and a["dimtype"] == b["dimtype"]
            if not (sm or st or same_tpl):
                continue
            mods.append({"type": "DIMENSION",
                         "kind": ("both" if (not sm and not st) else ("measurement" if not sm else "text")),
                         "layer": a["layer"],
                         "a_m": a["measurement"], "b_m": b["measurement"],
                         "a_txt": a["display_text"], "b_txt": b["display_text"],
                         "a_h": a["handle"], "b_h": b["handle"]})
            used.add(j); best = j
            break
    return mods, used


# ----------------------------------------------------------------------
# 主 diff
# ----------------------------------------------------------------------
def diff(base_path, edit_path):
    A, da = load(base_path)
    B, db = load(edit_path)
    rep = {"base": base_path, "edited": edit_path, "tol": TOL,
           "provenance": "同源读回 ezdxf（非独立交叉验证）", "findings": []}

    def add(mod, cls, note):
        mod = dict(mod); mod["suggested_class"] = cls; mod["note"] = note
        rep["findings"].append(mod)

    def section(type_name, keyfn, pairfn, clsfn):
        ka = [keyfn(e) for e in A[type_name]]
        kb = [keyfn(e) for e in B[type_name]]
        ca, cb = Counter(ka), Counter(kb)
        common = ca & cb
        removed = _subset(A[type_name], ka, ca, common)
        added = _subset(B[type_name], kb, cb, common)
        mods, used = pairfn(removed, added)
        for m in mods:
            c, n = clsfn(m); add(m, c, n)
        used_a = {m.get("a_h") for m in mods}
        used_b = {m.get("b_h") for m in mods}
        for e in removed:
            if e["handle"] not in used_a:
                c, n = clsfn({**e, "kind": "removed",
                              "a_dia": e.get("diameter"), "a_c": e.get("center"),
                              "a_text": e.get("text"), "a_pos": e.get("pos"),
                              "a_m": e.get("measurement"),
                              "a_txt": e.get("display_text"),
                              "a_pts": e.get("pts"), "layer": e["layer"]})
                add({**{k: v for k, v in e.items() if k != "handle"}, "type": type_name}, c, n)
        for e in added:
            if e["handle"] not in used_b:
                c, n = clsfn({**e, "kind": "added",
                              "b_dia": e.get("diameter"), "b_c": e.get("center"),
                              "b_text": e.get("text"), "b_pos": e.get("pos"),
                              "b_m": e.get("measurement"),
                              "b_txt": e.get("display_text"),
                              "b_pts": e.get("pts"), "layer": e["layer"]})
                add({**{k: v for k, v in e.items() if k != "handle"}, "type": type_name}, c, n)

    section("CIRCLE", _kc, _pair_circles, _classify_circle)
    section("LINE", _kl, _pair_lines, _classify_line)
    section("TEXT", _kt, _pair_texts, _classify_text)
    section("DIMENSION", _kd, _pair_dims, _classify_dim)

    # 其它实体：数量核对
    ta = Counter(x["type"] for x in A["OTHER"])
    tb = Counter(x["type"] for x in B["OTHER"])
    for t in sorted(set(ta) | set(tb)):
        if ta.get(t, 0) != tb.get(t, 0):
            add({"type": "OTHER", "subtype": t, "a_count": ta.get(t, 0), "b_count": tb.get(t, 0)},
                "ambiguous", f"{t} 数量 {ta.get(t,0)}→{tb.get(t,0)}（本工具未建模该类型，需人工核对）")

    # 图层属性核对（线型/线宽/颜色变化 → 外观级）
    la = {l.dxf.name: (l.dxf.linetype, l.dxf.lineweight, l.dxf.color) for l in da.layers}
    lb = {l.dxf.name: (l.dxf.linetype, l.dxf.lineweight, l.dxf.color) for l in db.layers}
    for n in sorted(set(la) | set(lb)):
        if la.get(n) != lb.get(n):
            add({"type": "LAYER", "name": n, "a": la.get(n), "b": lb.get(n)},
                "appearance", f"图层 {n} 属性 {la.get(n)}→{lb.get(n)}（线型/线宽/颜色，呈现）")

    return rep


# ----------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------
_CLS_TAG = {"intent": "意图级", "appearance": "外观级", "ambiguous": "歧义·应询问用户"}


def _fmt(m):
    t = m.get("type"); tag = _CLS_TAG.get(m.get("suggested_class"), m.get("suggested_class"))
    parts = [f"[{tag}] {t}"]
    if t == "CIRCLE":
        parts.append(f"层={m.get('layer')} Ø{m.get('a_dia', m.get('diameter'))}→{m.get('b_dia', m.get('diameter'))}")
        if m.get("kind") in ("center", "both") or m.get("a_c") != m.get("b_c"):
            parts.append(f"圆心 {m.get('a_c', m.get('center'))}→{m.get('b_c', m.get('center'))}")
    elif t == "LINE":
        parts.append(f"层={m.get('layer')} 端点 {m.get('a_pts', m.get('pts'))}→{m.get('b_pts', m.get('pts'))} 位移={m.get('dist', '-')}")
    elif t == "TEXT":
        parts.append(f"{m.get('a_text', m.get('text'))!r}→{m.get('b_text', m.get('text'))!r} 位置 {m.get('a_pos', m.get('pos'))}→{m.get('b_pos', m.get('pos'))}")
    elif t == "DIMENSION":
        parts.append(f"测量 {m.get('a_m', m.get('measurement'))}→{m.get('b_m', m.get('measurement'))} 文本 {m.get('a_txt', m.get('display_text'))!r}→{m.get('b_txt', m.get('display_text'))!r}")
    elif t == "LAYER":
        parts.append(f"{m.get('name')} {m.get('a')}→{m.get('b')}")
    elif t == "OTHER":
        parts.append(f"{m.get('subtype')} {m.get('a_count')}→{m.get('b_count')}")
    return f"{parts[0]}  |  {'  '.join(parts[1:])}  ||  {m.get('note','')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("edited"); ap.add_argument("--json", default=None)
    args = ap.parse_args()
    rep = diff(args.base, args.edited)
    f = rep["findings"]
    print(f"=== dxf_diff  {rep['base']}  vs  {rep['edited']}  (TOL={TOL}) ===")
    print(f"差异条数: {len(f)}    来源: {rep['provenance']}\n")
    bycls = Counter(x["suggested_class"] for x in f)
    print("分类汇总:", dict(bycls), "\n")
    for m in f:
        print(_fmt(m))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n[JSON 已写] {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
