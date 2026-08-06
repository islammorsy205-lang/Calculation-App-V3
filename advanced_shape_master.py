# advanced_shape_master.py

import streamlit as st
import numpy as np
import pandas as pd
import io
import os
import re
import math
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from PIL import Image, ImageChops
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

try:
    import ezdxf
except ImportError:
    st.error("⚠️ مكتبة 'ezdxf' غير موجودة! برجاء كتابة الأمر 'pip install ezdxf' في التيرمينال لتفعيل ميزة قراءة الكاد.")
    ezdxf = None

try:
    from config import SECTIONS_DB, STRUTS_DB
    from report_builder import insert_blue_banner, add_eq, append_pdf_stream_to_word
except ImportError:
    st.error("⚠️ برجاء التأكد من وجود ملفات config.py و report_builder.py")

# =========================================================
# 0. Helper Functions & Styles
# =========================================================
def apply_plot_styles():
    mpl.rcParams['font.family'] = 'sans-serif'
    mpl.rcParams['font.sans-serif'] = ['Arial', 'Helvetica', 'DejaVu Sans']
    mpl.rcParams['axes.linewidth'] = 0.3
    mpl.rcParams['font.size'] = 7
    mpl.rcParams['font.weight'] = 'normal'

def get_short_name(sec_name):
    return re.sub(r'\s*\(.*?\)', '', sec_name).strip()

def crop_image_bbox(img_bytes):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 0))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox:
        img = img.crop(bbox)
    out = io.BytesIO()
    img.save(out, format='PNG')
    return out.getvalue()

def safe_render_fig(fig):
    try:
        plt.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=400, bbox_inches='tight', pad_inches=0.0, transparent=True)
        return crop_image_bbox(buf.getvalue())
    finally:
        plt.close(fig)

def draw_reaction_arrow(ax, node_x, node_y, force_mag, axis_nx, axis_ny):
    if abs(force_mag) < 0.1: return
    arr_L = 0.5  
    sgn = np.sign(force_mag)
    dx = sgn * axis_nx
    dy = sgn * axis_ny
    start_x = node_x - arr_L * dx
    start_y = node_y - arr_L * dy
    arr_c = 'blue' if force_mag >= 0 else 'red'
    ax.arrow(start_x, start_y, arr_L*dx, arr_L*dy, length_includes_head=True, 
             head_width=0.08, head_length=0.12, fc=arr_c, ec=arr_c, lw=0.8, zorder=5)
    ax.text(start_x - 0.15*dx, start_y - 0.15*dy, f"{force_mag:+.1f}", 
            color=arr_c, fontsize=7, fontname='Arial', ha='center', va='center')

# =========================================================
# 1. DXF Parsing Engine (BIM Integration)
# =========================================================
def parse_dxf_to_data(file_bytes):
    if ezdxf is None: return None
    try:
        doc = ezdxf.read(io.BytesIO(file_bytes))
        msp = doc.modelspace()
        
        raw_frames = []
        raw_struts = []
        raw_supports = []
        
        def match_layer(layer, target):
            l_clean = layer.lower().replace(" ", "").replace("_", "")
            return target in l_clean
        
        for e in msp:
            lyr = e.dxf.layer
            etype = e.dxftype()
            
            # Supports (Points, Circles, Blocks)
            if match_layer(lyr, "supp"):
                if etype in ['POINT', 'CIRCLE', 'INSERT']:
                    if etype == 'POINT':
                        raw_supports.append({'x': e.dxf.location.x, 'y': e.dxf.location.y})
                    elif etype == 'CIRCLE':
                        raw_supports.append({'x': e.dxf.center.x, 'y': e.dxf.center.y})
                    elif etype == 'INSERT':
                        raw_supports.append({'x': e.dxf.insert.x, 'y': e.dxf.insert.y})
            
            # Struts (Lines or Polylines)
            elif match_layer(lyr, "push") or match_layer(lyr, "pull"):
                entities = [e]
                if etype in ['LWPOLYLINE', 'POLYLINE']:
                    entities = list(e.virtual_entities())
                for sub_e in entities:
                    if sub_e.dxftype() == 'LINE':
                        raw_struts.append({'p1': (sub_e.dxf.start.x, sub_e.dxf.start.y), 'p2': (sub_e.dxf.end.x, sub_e.dxf.end.y)})
                    
            # Frames (Lines, Arcs, Polylines)
            elif match_layer(lyr, "frame"):
                entities = [e]
                if etype in ['LWPOLYLINE', 'POLYLINE']:
                    entities = list(e.virtual_entities())
                
                for sub_e in entities:
                    if sub_e.dxftype() == 'LINE':
                        raw_frames.append({'type': 'line', 'p1': (sub_e.dxf.start.x, sub_e.dxf.start.y), 'p2': (sub_e.dxf.end.x, sub_e.dxf.end.y)})
                    elif sub_e.dxftype() == 'ARC':
                        c = sub_e.dxf.center
                        r = sub_e.dxf.radius
                        sa = math.radians(sub_e.dxf.start_angle)
                        ea = math.radians(sub_e.dxf.end_angle)
                        p1 = (c.x + r*math.cos(sa), c.y + r*math.sin(sa))
                        p2 = (c.x + r*math.cos(ea), c.y + r*math.sin(ea))
                        raw_frames.append({'type': 'arc', 'p1': p1, 'p2': p2, 'c': (c.x, c.y), 'r': r, 'sa': sa, 'ea': ea})

        if not raw_frames: return None
        
        min_y = min(min(f['p1'][1], f['p2'][1]) for f in raw_frames)
        real_frames = []
        base_len = 0.0
        for f in raw_frames:
            if f['type'] == 'line':
                if abs(f['p1'][1] - min_y) < 0.1 and abs(f['p2'][1] - min_y) < 0.1:
                    bl = abs(f['p1'][0] - f['p2'][0])
                    if bl > base_len: base_len = bl
                    continue
            real_frames.append(f)
            
        raw_frames = real_frames
        if not raw_frames: return None

        all_pts = []
        for f in raw_frames: all_pts.extend([f['p1'], f['p2']])
        start_pt = min(all_pts, key=lambda p: (round(p[1],2), round(p[0],2)))
        
        chained_segs = []
        curr_pt = start_pt
        curr_th = None
        used_idx = set()
        
        for _ in range(len(raw_frames)):
            best_idx, best_dist, best_is_p1 = -1, 999.0, True
            for i, f in enumerate(raw_frames):
                if i in used_idx: continue
                d1 = math.hypot(f['p1'][0]-curr_pt[0], f['p1'][1]-curr_pt[1])
                d2 = math.hypot(f['p2'][0]-curr_pt[0], f['p2'][1]-curr_pt[1])
                if d1 < best_dist: best_idx, best_dist, best_is_p1 = i, d1, True
                if d2 < best_dist: best_idx, best_dist, best_is_p1 = i, d2, False
                
            if best_idx == -1 or best_dist > 0.5: break
            used_idx.add(best_idx)
            f = raw_frames[best_idx]
            
            p_start = f['p1'] if best_is_p1 else f['p2']
            p_end = f['p2'] if best_is_p1 else f['p1']
            
            if f['type'] == 'line':
                dx, dy = p_end[0]-p_start[0], p_end[1]-p_start[1]
                L = math.hypot(dx, dy)
                ang = math.degrees(math.atan2(dy, dx))
                smooth = False if curr_th is None else (abs((ang - curr_th + 180)%360 - 180) < 5.0)
                chained_segs.append({'Shape Type': 'Straight Line', 'L': L, 'start_angle': ang, 'smooth': smooth})
                curr_th = ang
                curr_pt = p_end
            elif f['type'] == 'arc':
                arc_p1 = (f['c'][0] + f['r']*math.cos(f['sa']), f['c'][1] + f['r']*math.sin(f['sa']))
                is_ccw = math.hypot(p_start[0]-arc_p1[0], p_start[1]-arc_p1[1]) < 0.1
                
                ang_diff = (f['ea'] - f['sa']) % (2*math.pi)
                if ang_diff == 0: ang_diff = 2*math.pi

                if is_ccw:
                    tangent_ang = f['sa'] + math.pi/2
                    dir_crv = "Arching Up ⤴ (Concave)"
                    L_arc = f['r'] * ang_diff
                else:
                    tangent_ang = f['ea'] - math.pi/2
                    dir_crv = "Arching Down ⤵ (Convex)"
                    ang_diff = 2*math.pi - ang_diff
                    L_arc = f['r'] * ang_diff
                    
                tangent_deg = math.degrees(tangent_ang) % 360
                smooth = False if curr_th is None else (abs((tangent_deg - curr_th + 180)%360 - 180) < 5.0)
                
                chained_segs.append({'Shape Type': 'Curve (Arc & Radius)', 'L': L_arc, 'Radius (R) (m)': f['r'], 'Curvature Direction': dir_crv, 'start_angle': tangent_deg, 'smooth': smooth})
                curr_th = tangent_deg + math.degrees(ang_diff) if is_ccw else tangent_deg - math.degrees(ang_diff)
                curr_pt = p_end

        struts_mapped = []
        for s in raw_struts:
            p_ground = s['p1'] if s['p1'][1] < s['p2'][1] else s['p2']
            p_top = s['p2'] if s['p1'][1] < s['p2'][1] else s['p1']
            
            best_seg, best_s, min_d = 0, 0.0, 999.0
            tr_x, tr_y = start_pt[0], start_pt[1]
            tr_th = math.radians(chained_segs[0]['start_angle'])
            
            for idx, seg in enumerate(chained_segs):
                L = seg['L']
                if seg['Shape Type'] == 'Straight Line': kappa = 0.0
                else:
                    r_val = seg['Radius (R) (m)']
                    kappa = -1.0/r_val if "Down" in seg['Curvature Direction'] else 1.0/r_val
                
                steps = max(10, int(L / 0.1))
                for step in range(steps + 1):
                    s_test = (step / steps) * L
                    px, py, _ = get_parametric_point(tr_x, tr_y, tr_th, kappa, s_test)
                    d = math.hypot(px - p_top[0], py - p_top[1])
                    if d < min_d:
                        min_d = d
                        best_seg = idx
                        best_s = s_test
                
                tr_x, tr_y, tr_th = get_parametric_point(tr_x, tr_y, tr_th, kappa, L)
            
            struts_mapped.append({'seg_idx': best_seg, 'dist': best_s, 'gx': p_ground[0] - start_pt[0]})

        supps_mapped = []
        for sp in raw_supports:
            supps_mapped.append({'x': sp['x'] - start_pt[0], 'type': 'Hinged'})

        return {'segments': chained_segs, 'struts': struts_mapped, 'supports': supps_mapped, 'base_length': base_len}
        
    except Exception as e:
        st.error(f"⚠️ خطأ أثناء تحليل ملف الـ DXF: {e}")
        return None

# =========================================================
# 2. Geometry & Mesh Generators (Parametric Chain Engine)
# =========================================================
def get_parametric_point(x0, y0, th0, kappa, s):
    if abs(kappa) < 1e-6: 
        x = x0 + s * np.cos(th0)
        y = y0 + s * np.sin(th0)
        th = th0
    else: 
        x = x0 + (np.sin(th0 + kappa * s) - np.sin(th0)) / kappa
        y = y0 - (np.cos(th0 + kappa * s) - np.cos(th0)) / kappa
        th = th0 + kappa * s
    return x, y, th

def build_chain_mesh(segments, seg_sections, loads, struts, base_sec, supports, corner_sup, base_length=0.0):
    nodes = []
    elements = []
    nodal_loads = []
    
    node_tol = 1e-4
    def get_or_add_node(x, y):
        for i, n in enumerate(nodes):
            if abs(n[0] - x) < node_tol and abs(n[1] - y) < node_tol:
                return i
        nodes.append([x, y])
        return len(nodes) - 1

    seg_start_data = [] 
    curr_x, curr_y = 0.0, 0.0
    curr_th = np.radians(corner_sup.get('angle', 0.0)) 
    
    key_nodes = set()
    
    for i, seg in enumerate(segments):
        if i == 0 or not seg.get('smooth', True):
            curr_th = np.radians(seg['start_angle'])
            
        L = seg['L']
        kappa = seg['kappa']
        seg_start_data.append({'x0': curr_x, 'y0': curr_y, 'th0': curr_th, 'kappa': kappa})
        
        key_s_vals = [0.0, L]
        for st_item in struts:
            if st_item['seg_idx'] == i: key_s_vals.append(st_item['s_dist'])
        for ld in loads:
            if ld['seg_idx'] == i:
                key_s_vals.append(ld['start'])
                key_s_vals.append(ld['end'])
        
        keys = list(key_s_vals)
        num_sub = max(1, int(np.ceil(L / 0.25)))
        for p in np.linspace(0, L, num_sub+1): keys.append(p)
            
        keys = sorted(list(set([round(k, 4) for k in keys if 0 <= k <= L + 1e-5])))
        
        node_indices = []
        for s_val in keys:
            px, py, _ = get_parametric_point(curr_x, curr_y, curr_th, kappa, s_val)
            nid = get_or_add_node(px, py)
            node_indices.append(nid)
            if any(abs(s_val - kv) < 1e-4 for kv in key_s_vals):
                key_nodes.add(nid)
            
        sec_props = seg_sections[i]
        
        for j in range(len(keys)-1):
            n1, n2 = node_indices[j], node_indices[j+1]
            s_mid = (keys[j] + keys[j+1]) / 2.0
            _, _, th_mid = get_parametric_point(curr_x, curr_y, curr_th, kappa, s_mid)
            
            p_x1, p_y1, p_x2, p_y2 = 0.0, 0.0, 0.0, 0.0
            
            for ld in loads:
                if ld['seg_idx'] == i and ld['type'] != 'Point Load':
                    if ld['start'] - 1e-4 <= s_mid <= ld['end'] + 1e-4:
                        L_ld = max(ld['end'] - ld['start'], 1e-5)
                        wa = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j] - ld['start']) / L_ld
                        wb = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j+1] - ld['start']) / L_ld
                        
                        if ld['dir'] == 'Gravity (Vertical ↓)':
                            p_y1 -= wa; p_y2 -= wb
                        else: 
                            c_t, s_t = np.cos(th_mid), np.sin(th_mid)
                            p_x1 += wa * s_t; p_y1 -= wa * c_t
                            p_x2 += wb * s_t; p_y2 -= wb * c_t
                            
            elements.append({
                'type': 'frame', 'group': 'segment', 'sec': sec_props['name'],
                'n1': n1, 'n2': n2, 'px1': p_x1, 'py1': p_y1, 'px2': p_x2, 'py2': p_y2,
                'E': sec_props['E'] * 10000.0, 'A': sec_props['A'], 'I': sec_props['I'] / 100000000.0,
                'seg_idx': i
            })
            
        for ld in loads:
            if ld['seg_idx'] == i and ld['type'] == 'Point Load':
                try:
                    idx = keys.index(round(ld['start'], 4))
                    nid = node_indices[idx]
                    if ld['dir'] == 'Gravity (Vertical ↓)':
                        nodal_loads.append({'node': nid, 'Fx': 0.0, 'Fy': -ld['w1']})
                    else:
                        _, _, th_pt = get_parametric_point(curr_x, curr_y, curr_th, kappa, ld['start'])
                        c_t, s_t = np.cos(th_pt), np.sin(th_pt)
                        nodal_loads.append({'node': nid, 'Fx': ld['w1']*s_t, 'Fy': -ld['w1']*c_t})
                except ValueError: pass
                
        curr_x, curr_y, curr_th = get_parametric_point(curr_x, curr_y, curr_th, kappa, L)

    base_x_pts = [0.0]
    if base_length > 0.0: base_x_pts.append(base_length)
    for n in nodes: base_x_pts.append(n[0])
    for st_item in struts: base_x_pts.append(st_item['gx'])
    for sup in supports: base_x_pts.append(sup['x'])
    
    base_x_pts = sorted(list(set([round(x, 4) for x in base_x_pts])))
    base_node_map = {}
    for x in base_x_pts:
        base_node_map[x] = get_or_add_node(x, 0.0)
        
    if base_sec != "None (Direct to Ground)":
        b_props = SECTIONS_DB.get(base_sec, {'E': 2100.0, 'A': 0.00343, 'I': 412.0})
        for j in range(len(base_x_pts)-1):
            n1 = base_node_map[base_x_pts[j]]
            n2 = base_node_map[base_x_pts[j+1]]
            elements.append({
                'type': 'frame', 'group': 'base', 'sec': base_sec,
                'n1': n1, 'n2': n2, 'px1': 0.0, 'py1': 0.0, 'px2': 0.0, 'py2': 0.0,
                'E': b_props.get('E', 2100.0) * 10000.0, 'A': b_props.get('A', b_props.get('A_cm2', 34.3)/10000.0), 
                'I': b_props.get('I', b_props.get('I_cm4', 412.0)) / 100000000.0
            })

    for st_idx, st_item in enumerate(struts):
        seg_idx = st_item['seg_idx']
        dist = st_item['s_dist']
        gx = st_item['gx']
        
        s_data = seg_start_data[seg_idx]
        nx, ny, _ = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], dist)
        
        top_node = get_or_add_node(nx, ny)
        bot_node = get_or_add_node(gx, 0.0)
        
        elements.append({
            'type': 'truss', 'group': 'strut', 'sec': st_item['sec'],
            'n1': bot_node, 'n2': top_node, 'strut_idx': st_idx,
            'E': 21000000.0, 'A': 0.001
        })

    supports_list = []
    supports_list.append({'node': 0, 'type': corner_sup['type'], 'angle': corner_sup['angle']})
    for sup in supports:
        nid = get_or_add_node(sup['x'], 0.0)
        supports_list.append({'node': nid, 'type': sup['type'], 'angle': 0.0})
        
    display_nodes = set([s['node'] for s in supports_list])
    display_nodes.update(key_nodes) 

    return nodes, elements, nodal_loads, display_nodes, supports_list, seg_start_data

# =========================================================
# 3. Advanced FEA Solver
# =========================================================
def solve_fea_engine(nodes, elements, nodal_loads, supports_list):
    NDOF = len(nodes) * 3
    K = np.zeros((NDOF, NDOF))
    F = np.zeros(NDOF)
    
    for el in elements:
        n1, n2 = el['n1'], el['n2']
        x1, y1 = nodes[n1]
        x2, y2 = nodes[n2]
        L = np.hypot(x2 - x1, y2 - y1)
        if L < 1e-5: continue
        c, s = (x2 - x1) / L, (y2 - y1) / L
        el['L'], el['c'], el['s'] = L, c, s
        
        E, A, I = el['E'], el['A'], el.get('I', 0.00005)
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]
        ])
        
        if el['type'] == 'truss':
            k_loc = np.zeros((6, 6))
            k_loc[0,0] = k_loc[3,3] = E*A/L
            k_loc[0,3] = k_loc[3,0] = -E*A/L
        else:
            k_loc = np.array([
                [E*A/L, 0, 0, -E*A/L, 0, 0], [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L], [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2], [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/L**2, 4*E*I/L]
            ])
            px1, py1, px2, py2 = el.get('px1',0), el.get('py1',0), el.get('px2',0), el.get('py2',0)
            f_loc = np.array([
                (2*px1 + px2)*L/6.0, (7*py1 + 3*py2)*L/20.0, (3*py1 + 2*py2)*L**2/60.0,
                (px1 + 2*px2)*L/6.0, (3*py1 + 7*py2)*L/20.0, -(2*py1 + 3*py2)*L**2/60.0
            ])
            f_glob = T.T @ f_loc
            dof = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
            for r in range(6): F[dof[r]] += f_glob[r]
            
        k_glob = T.T @ k_loc @ T
        dof = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
        for r in range(6):
            for col in range(6): K[dof[r], dof[col]] += k_glob[r, col]
                
    for nl in nodal_loads:
        F[3*nl['node']] += nl['Fx']
        F[3*nl['node']+1] += nl['Fy']
            
    K_orig = K.copy()
    fixed_dofs = []
    K_pen = 1e12
    
    for sup in supports_list:
        n, t, a = sup['node'], sup['type'], sup['angle']
        if t == 'Fixed': fixed_dofs.extend([3*n, 3*n+1, 3*n+2])
        elif t == 'Hinged': fixed_dofs.extend([3*n, 3*n+1])
        elif t == 'Roller':
            rad = np.radians(a)
            nx, ny = -np.sin(rad), np.cos(rad) 
            K[3*n, 3*n] += K_pen*nx**2; K[3*n+1, 3*n+1] += K_pen*ny**2
            K[3*n, 3*n+1] += K_pen*nx*ny; K[3*n+1, 3*n] += K_pen*nx*ny

    free_dof = [i for i in range(NDOF) if i not in fixed_dofs]
    K_ff = K[np.ix_(free_dof, free_dof)]
    F_f = F[free_dof]
    
    U = np.zeros(NDOF)
    try: U[free_dof] = np.linalg.solve(K_ff, F_f)
    except: U[free_dof] = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    R_reactions = K_orig @ U - F 
    
    for el in elements:
        if 'L' not in el: continue
        n1, n2 = el['n1'], el['n2']
        c, s, L = el['c'], el['s'], el['L']
        E, A, I = el['E'], el['A'], el.get('I', 0.00005)
        
        u_glob = U[[3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]]
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]
        ])
        u_loc = T @ u_glob
        el['internal'] = {'u_loc': u_loc}
        
        if el['type'] == 'truss':
            N_val = (E * A / L) * (u_loc[3] - u_loc[0])
            el['internal'].update({'N': [N_val, N_val], 'V': [0,0], 'M': [0,0], 'x': [0, L], 'v_rel': [0,0]})
        else:
            k_loc = np.array([
                [E*A/L, 0, 0, -E*A/L, 0, 0], [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L], [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2], [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/L**2, 4*E*I/L]
            ])
            px1, py1, px2, py2 = el.get('px1',0), el.get('py1',0), el.get('px2',0), el.get('py2',0)
            f_loc = np.array([
                (2*px1 + px2)*L/6.0, (7*py1 + 3*py2)*L/20.0, (3*py1 + 2*py2)*L**2/60.0,
                (px1 + 2*px2)*L/6.0, (3*py1 + 7*py2)*L/20.0, -(2*py1 + 3*py2)*L**2/60.0
            ])
            f_end = k_loc @ u_loc - f_loc
            
            xs = np.linspace(0, L, 51) 
            N_arr, V_arr, M_arr, v_rel_arr = np.zeros_like(xs), np.zeros_like(xs), np.zeros_like(xs), np.zeros_like(xs)
            v1, theta1, v2, theta2 = u_loc[1], u_loc[2], u_loc[4], u_loc[5]
            
            for i, x in enumerate(xs):
                N_arr[i] = -f_end[0] - (px1*x + (px2-px1)*x**2/(2*L))
                V_arr[i] = f_end[1] + (py1*x + (py2-py1)*x**2/(2*L))
                M_arr[i] = -f_end[2] + f_end[1]*x + py1*x**2/2.0 + (py2-py1)*x**3/(6*L)
                
                xi = x / L
                v_shape = v1*(1-3*xi**2+2*xi**3) + theta1*(x*(1-xi)**2) + v2*(3*xi**2-2*xi**3) + theta2*(x*(xi**2-xi))
                v_chord = v1 + xi * (v2 - v1) 
                v_rel_arr[i] = v_shape - v_chord 
                
            el['internal'].update({'N': N_arr, 'V': V_arr, 'M': M_arr, 'x': xs, 'v_rel': v_rel_arr})
            
    return U, R_reactions

# =========================================================
# 4. Plotting Engine (Live Preview & SAP2000 Style)
# =========================================================
def draw_base_geometry(ax, nodes, elements, supports_list, seg_sections=None, segments=None, seg_starts=None):
    for el in elements:
        if el['type'] not in ['frame', 'truss']: continue
        n1, n2 = nodes[el['n1']], nodes[el['n2']]
        if el['type'] == 'truss':
            ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color='gray', linestyle='--', linewidth=1.0, zorder=1)
        else:
            if el.get('group') == 'base' and el.get('sec') == "None (Direct to Ground)": continue
            if el.get('group') == 'segment' and segments and seg_starts:
                s_idx = el['seg_idx']
                seg = segments[s_idx]
                s_data = seg_starts[s_idx]
                curve_x, curve_y = [], []
                for p in np.linspace(0, el['L'], 10):
                    cx, cy, _ = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], p)
                    curve_x.append(cx)
                    curve_y.append(cy)
                ax.plot(curve_x, curve_y, color='black', linestyle='-', linewidth=1.5, zorder=1)
            else:
                ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color='black', linestyle='-', linewidth=1.5, zorder=1)
            
    for sup in supports_list:
        n = sup['node']
        x, y = nodes[n][0], nodes[n][1]
        t = sup['type']
        a = sup.get('angle', 0.0)
        
        rad = np.radians(a)
        nx, ny = np.sin(rad), -np.cos(rad) 
        tx, ty = -ny, nx 
        
        if t == 'Fixed':
            ax.plot(x, y, marker='s', markerfacecolor='none', markeredgecolor='limegreen', markersize=3, zorder=5)
            ax.plot([x - 0.1*tx, x + 0.1*tx], [y - 0.1*ty, y + 0.1*ty], color='limegreen', lw=1.5, zorder=4)
        elif t == 'Hinged':
            h, w = 0.1, 0.07
            p1 = (x, y)
            p2 = (x + h*nx + w*tx, y + h*ny + w*ty)
            p3 = (x + h*nx - w*tx, y + h*ny - w*ty)
            ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            ax.plot([p2[0]+0.05*tx, p3[0]-0.05*tx], [p2[1]+0.05*ty, p3[1]-0.05*ty], color='limegreen', lw=1.2, zorder=4)
        elif t == 'Roller':
            h, w, r = 0.1, 0.07, 0.03
            p1 = (x, y)
            p2 = (x + h*nx + w*tx, y + h*ny + w*ty)
            p3 = (x + h*nx - w*tx, y + h*ny - w*ty)
            ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            circ_x, circ_y = x + (h + r)*nx, y + (h + r)*ny
            ax.add_patch(plt.Circle((circ_x, circ_y), r, facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            base_dist = h + 2*r
            line_w = 0.075
            lx1, ly1 = x + base_dist*nx - line_w*tx, y + base_dist*ny - line_w*ty
            lx2, ly2 = x + base_dist*nx + line_w*tx, y + base_dist*ny + line_w*ty
            ax.plot([lx1, lx2], [ly1, ly2], color='limegreen', lw=1.2, zorder=4)

    if seg_sections and segments and seg_starts:
        for el in elements:
            if el['type'] == 'truss':
                n1, n2 = nodes[el['n1']], nodes[el['n2']]
                mid_x, mid_y = (n1[0]+n2[0])/2, (n1[1]+n2[1])/2
                dx, dy = n2[0]-n1[0], n2[1]-n1[1]
                rot = np.degrees(np.arctan2(dy, dx))
                if rot > 90: rot -= 180
                elif rot < -90: rot += 180
                L_hyp = np.hypot(dx, dy)
                if L_hyp > 1e-4:
                    nx_s, ny_s = -dy/L_hyp, dx/L_hyp
                    st_id = el.get('strut_idx', 0) + 1
                    label = f"P{st_id}: {get_short_name(el.get('sec', ''))}"
                    ax.text(mid_x + nx_s*0.1, mid_y + ny_s*0.1, label, 
                            color='dimgray', fontsize=6, rotation=rot, ha='center', va='center', fontname='Arial')
        
        for i, seg in enumerate(segments):
            s_data = seg_starts[i]
            mx, my, mth = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], seg['L']/2)
            rot_deg = np.degrees(mth)
            if rot_deg > 90: rot_deg -= 180
            elif rot_deg < -90: rot_deg += 180
            sec_name = seg_sections[i]['name']
            label = f"S{i+1}: {get_short_name(sec_name)}"
            ax.text(mx - np.sin(mth)*0.1, my + np.cos(mth)*0.1, label, 
                    color='dimgray', fontsize=6, ha='center', va='center', rotation=rot_deg, fontname='Arial')

def draw_loads_and_geometry(ax, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts):
    draw_base_geometry(ax, nodes, elements, supports_list, seg_sections, segments, seg_starts)

    scale_ld = 0.05
    for ld in loads:
        i = ld['seg_idx']
        s_data = seg_starts[i]
        w1, w2 = ld['w1'], ld['w2']
        
        num_pts = max(10, int((ld['end'] - ld['start']) / 0.1))
        s_vals = np.linspace(ld['start'], ld['end'], num_pts)
        poly_pts = []
        top_pts = []
        
        for sv in s_vals:
            px, py, th = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], sv)
            w_curr = w1 + (w2 - w1) * (sv - ld['start']) / max(1e-5, (ld['end'] - ld['start']))
            hl = w_curr * scale_ld
            poly_pts.append((px, py))
            if ld['dir'] == 'Gravity (Vertical ↓)':
                top_pts.append((px, py + hl))
            else:
                c, s = np.cos(th), np.sin(th)
                top_pts.append((px - s*hl, py + c*hl))
                
        poly_pts.extend(top_pts[::-1])
        ax.add_patch(Polygon(poly_pts, facecolor='none', edgecolor='blue', lw=0.8, zorder=2))

        num_arrows = max(3, int((ld['end'] - ld['start']) / 0.8))
        for k in range(num_arrows):
            frac = k / (num_arrows - 1) if num_arrows > 1 else 0.5
            sv = ld['start'] + frac * (ld['end'] - ld['start'])
            px_c, py_c, th_c = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], sv)
            w_curr = w1 + frac * (w2 - w1)
            hl = w_curr * scale_ld
            arr_len = hl * 0.6 
            
            if ld['dir'] == 'Gravity (Vertical ↓)':
                ax.arrow(px_c, py_c + arr_len, 0, -arr_len, head_width=0.05, head_length=0.1, length_includes_head=True, fc='blue', ec='blue', lw=0.5, zorder=3)
            else:
                c_c, s_c = np.cos(th_c), np.sin(th_c)
                ax.arrow(px_c - s_c*arr_len, py_c + c_c*arr_len, s_c*arr_len, -c_c*arr_len, head_width=0.05, head_length=0.1, length_includes_head=True, fc='blue', ec='blue', lw=0.5, zorder=3)

        px1, py1, th1 = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], ld['start'])
        hx1 = px1 if ld['dir'] == 'Gravity (Vertical ↓)' else px1 - np.sin(th1)*w1*scale_ld
        hy1 = py1 + w1*scale_ld if ld['dir'] == 'Gravity (Vertical ↓)' else py1 + np.cos(th1)*w1*scale_ld
        ax.text(hx1, hy1+0.2, f"{w1:.1f}", color='black', fontsize=6, ha='center', fontname='Arial')

        px2, py2, th2 = get_parametric_point(s_data['x0'], s_data['y0'], s_data['th0'], s_data['kappa'], ld['end'])
        hx2 = px2 if ld['dir'] == 'Gravity (Vertical ↓)' else px2 - np.sin(th2)*w2*scale_ld
        hy2 = py2 + w2*scale_ld if ld['dir'] == 'Gravity (Vertical ↓)' else py2 + np.cos(th2)*w2*scale_ld
        ax.text(hx2, hy2+0.2, f"{w2:.1f}", color='black', fontsize=6, ha='center', fontname='Arial')

def draw_live_preview(nodes, elements, supports_list, seg_sections, loads, segments, seg_starts):
    apply_plot_styles()
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    draw_loads_and_geometry(ax, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts)
    return safe_render_fig(fig)

def plot_sap2000_diagrams(nodes, elements, R_reactions, scales, display_nodes, supports_list, seg_sections, loads, segments, seg_starts):
    apply_plot_styles()
    figs_dict = {}
    
    fig_ld, ax_ld = plt.subplots(figsize=(6, 5))
    ax_ld.set_aspect('equal', adjustable='datalim')
    ax_ld.axis('off')
    draw_loads_and_geometry(ax_ld, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts)
    figs_dict['Load'] = safe_render_fig(fig_ld)
    
    fig_r, ax_r = plt.subplots(figsize=(6, 5))
    ax_r.set_aspect('equal', adjustable='datalim')
    ax_r.axis('off')
    draw_base_geometry(ax_r, nodes, elements, supports_list, seg_sections, segments, seg_starts)
    
    for sup in supports_list:
        n = sup['node']
        t = sup['type']
        a = sup['angle']
        Rx, Ry = R_reactions[3*n], R_reactions[3*n+1]
        x, y = nodes[n][0], nodes[n][1]
        
        rad = np.radians(a)
        c_a, s_a = np.cos(rad), np.sin(rad)
        R_u = Rx * c_a + Ry * s_a
        R_v = -Rx * s_a + Ry * c_a
        
        if t == 'Roller':
            draw_reaction_arrow(ax_r, x, y, R_v, -s_a, c_a)
        else:
            draw_reaction_arrow(ax_r, x, y, R_u, c_a, s_a)
            draw_reaction_arrow(ax_r, x, y, R_v, -s_a, c_a)
            
    figs_dict['React'] = safe_render_fig(fig_r)
    
    def create_force_plot(val_key, scale, c_pos, c_neg):
        fig_f, ax_f = plt.subplots(figsize=(6, 5))
        ax_f.set_aspect('equal', adjustable='datalim')
        ax_f.axis('off')
        draw_base_geometry(ax_f, nodes, elements, supports_list, seg_sections, segments, seg_starts)
        
        global_texts = []
        def is_far(tx, ty):
            for (px, py) in global_texts:
                if np.hypot(tx-px, ty-py) < 0.35: return False
            return True

        global_max = 0.0
        for el in elements:
            if el['type'] == 'frame' and el['group'] == 'segment': 
                global_max = max(global_max, np.max(np.abs(el['internal'][val_key])))

        for el in elements:
            if el['type'] != 'frame': continue
            n1, n2 = el['n1'], el['n2']
            x1, y1 = nodes[n1]
            c, s, L = el['c'], el['s'], el['L']
            
            xs = el['internal']['x']
            vals = el['internal'][val_key]
            plot_vals = -vals if val_key != 'N' else vals
            
            px = x1 + c * xs - s * plot_vals * scale
            py = y1 + s * xs + c * plot_vals * scale
            
            for k in range(len(px)-1):
                color = c_pos if vals[k] >= 0 else c_neg
                ax_f.plot([px[k], px[k+1]], [py[k], py[k+1]], color=color, lw=0.8)
                
            ax_f.plot([x1, px[0]], [y1, py[0]], color=c_pos if vals[0]>=0 else c_neg, lw=0.8)
            ax_f.plot([x1+c*L, px[-1]], [y1+s*L, py[-1]], color=c_pos if vals[-1]>=0 else c_neg, lw=0.8)

            num_lines = max(2, int(L / 0.4))
            for i in range(1, num_lines):
                idx = int(i * len(px) / num_lines)
                color = c_pos if vals[idx] >= 0 else c_neg
                lx, ly = x1 + c*xs[idx], y1 + s*xs[idx]
                ax_f.plot([lx, px[idx]], [ly, py[idx]], color=color, lw=0.3, alpha=0.6)

            def plot_val(idx):
                v = vals[idx]
                if abs(v) < 0.1: return
                tx, ty = px[idx], py[idx]
                sgn = 1 if plot_vals[idx] >= 0 else -1
                tx += -s * sgn * 0.15
                ty += c * sgn * 0.15
                v_color = c_pos if v >= 0 else c_neg
                if is_far(tx, ty):
                    ax_f.text(tx, ty, f"{v:+.1f}", fontsize=6, color=v_color, ha='center', va='center', fontname='Arial')
                    global_texts.append((tx, ty))

            if len(vals) > 0:
                if n1 in display_nodes: plot_val(0)
                if n2 in display_nodes: plot_val(-1)
                
                max_idx = np.argmax(np.abs(vals))
                if max_idx > 0 and max_idx < len(vals)-1:
                    if abs(vals[max_idx]) > global_max * 0.1: plot_val(max_idx)
                
        return safe_render_fig(fig_f)

    figs_dict['N'] = create_force_plot('N', scales['N'], 'blue', 'red')
    figs_dict['V'] = create_force_plot('V', scales['V'], 'blue', 'red')
    figs_dict['M'] = create_force_plot('M', scales['M'], 'blue', 'red')
    
    fig_d, ax_d = plt.subplots(figsize=(6, 5))
    ax_d.set_aspect('equal', adjustable='datalim')
    ax_d.axis('off')
    draw_base_geometry(ax_d, nodes, elements, supports_list, seg_sections, segments, seg_starts)
    
    max_def = 0.0
    for el in elements:
        if el['type'] == 'frame':
            xs = el['internal']['x']
            v_rel = el['internal']['v_rel'] * 20.0 
            x1, y1 = nodes[el['n1']]
            c, s = el['c'], el['s']
            px = x1 + c * xs - s * v_rel
            py = y1 + s * xs + c * v_rel
            ax_d.plot(px, py, color='red', linestyle='--', linewidth=1.2, alpha=0.8)
            max_def = max(max_def, np.max(np.abs(el['internal']['v_rel'])))
            
    if max_def > 0:
        ax_d.text(nodes[0][0], nodes[0][1]+1.0, f"Max Deflection = {max_def*1000:.2f} mm", color='red', fontsize=10, fontweight='bold')
    figs_dict['D'] = safe_render_fig(fig_d)

    return figs_dict

# =========================================================
# 5. Word Report Generator
# =========================================================
def generate_chain_report(sys_data):
    if os.path.exists("Acrow_Template.docx"):
        doc = Document("Acrow_Template.docx")
        doc.add_page_break()
    else:
        doc = Document()
        
    def force_ltr_left(p):
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pPr = p._element.get_or_add_pPr()
        bidi = OxmlElement('w:bidi')
        bidi.set(qn('w:val'), '0')
        pPr.append(bidi)
        
    def add_line(text, bold=False):
        p = doc.add_paragraph()
        force_ltr_left(p)
        p.paragraph_format.line_spacing = 1.5
        r = p.add_run(text)
        r.font.name = 'Arial'
        r.font.size = Pt(12)
        r.font.bold = bold
        r.font.rtl = False
        
    p_title = doc.add_paragraph()
    force_ltr_left(p_title)
    run_title = p_title.add_run("CALCULATION SHEET FOR ADVANCED SHAPES")
    run_title.font.name = 'Arial'
    run_title.font.size = Pt(16)
    run_title.font.bold = True
    run_title.font.rtl = False
    
    add_line("="*50, bold=True)
    
    add_line(f"1. Geometry & Inputs:", bold=True)
    add_line(f"- Total System Length = {sum([s['L'] for s in sys_data['segments']]):.2f} m")
    add_line(f"- Number of Segments = {len(sys_data['segments'])}")
    
    doc.add_paragraph()
    add_line(f"2. Safety Checks:", bold=True)
    
    for df_row in sys_data['safety_df']:
        add_line(f"- {df_row['Component']} ({df_row['Force Type']}): {df_row['Actual']} vs {df_row['Allowable']} => {df_row['Status']}")
    
    doc.add_page_break()
    add_line("3. Analysis Diagrams:", bold=True)
    doc.add_paragraph()
    
    def add_diagram_pair(doc, img1_bytes, title1, img2_bytes, title2):
        table = doc.add_table(rows=2, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        p1 = table.rows[0].cells[0].paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p1.add_run().add_picture(io.BytesIO(img1_bytes), width=Cm(8.0))
        
        p2 = table.rows[0].cells[1].paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.add_run().add_picture(io.BytesIO(img2_bytes), width=Cm(8.0))
        
        p3 = table.rows[1].cells[0].paragraphs[0]
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r3 = p3.add_run(title1)
        r3.font.name, r3.font.size, r3.bold = 'Arial', Pt(10), True
        
        p4 = table.rows[1].cells[1].paragraphs[0]
        p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r4 = p4.add_run(title2)
        r4.font.name, r4.font.size, r4.bold = 'Arial', Pt(10), True

    bufs = sys_data['img_bufs']
    add_diagram_pair(doc, bufs['Load'], "Assigned Load Diagram", bufs['React'], "Reactions Diagram (kN)")
    doc.add_paragraph()
    add_diagram_pair(doc, bufs['N'], "Axial Force Diagram (kN)", bufs['V'], "Shear Force Diagram (kN)")
    doc.add_paragraph()
    add_diagram_pair(doc, bufs['M'], "Bending Moment Diagram (kN.m)", bufs['D'], "Deflection Shape")
    
    out = io.BytesIO()
    doc.save(out)
    return out

def reset_adv_state():
    if 'adv_solved' in st.session_state:
        st.session_state.adv_solved = False

# =========================================================
# 6. Main Streamlit UI (The Hybrid Builder)
# =========================================================
def render_advanced_shape_module():
    st.markdown("## 🎢 The Chain Builder (Multi-Segment & CAD Integration)")
    
    if 'adv_solved' not in st.session_state:
        st.session_state.adv_solved = False

    st.info("💡 **Tip:** You can upload a DXF file to automatically fill in the geometry, supports, and struts!")
    uploaded_dxf = st.file_uploader("📥 Upload DXF File (.dxf)", type=['dxf'], key="dxf_uploader")
    
    if uploaded_dxf and st.button("Extract Data from DXF"):
        dxf_data = parse_dxf_to_data(uploaded_dxf.getvalue())
        if dxf_data:
            st.session_state.dxf_parsed = dxf_data
            st.success("✅ DXF Parsed Successfully! Fields below have been auto-filled.")
        else:
            st.error("❌ Failed to extract meaningful data. Check the format and try again.")

    dxf_data = st.session_state.get('dxf_parsed', None)

    # 💡 الشاشة منقسمة (1.2 مدخلات و 1.8 للرسمة عشان تبقى كبيرة جداً)
    c_in, c_plot = st.columns([1.2, 1.8])
    
    with c_in:
        st.markdown("### 1. Chain Segments Definition")
        def_segs = len(dxf_data['segments']) if dxf_data else 1
        num_segs = st.number_input("Number of Segments in Chain", min_value=1, max_value=20, value=def_segs, on_change=reset_adv_state)
        
        segments = []
        for i in range(int(num_segs)):
            with st.expander(f"⚙️ Segment S{i+1}", expanded=(num_segs<3)):
                s_type_idx = 0
                def_L, def_ang, def_R, def_S, def_smooth = 3.0, 60.0, 5.0, 3.0, True
                dir_crv_idx = 0
                if dxf_data and i < len(dxf_data['segments']):
                    d_seg = dxf_data['segments'][i]
                    if d_seg['Shape Type'] == 'Straight Line':
                        s_type_idx = 0
                        def_L = d_seg['L']
                        def_ang = d_seg['start_angle']
                        def_smooth = d_seg['smooth']
                    elif d_seg['Shape Type'] == 'Curve (Arc & Radius)':
                        s_type_idx = 1
                        def_R = d_seg['Radius (R) (m)']
                        def_S = d_seg['L']
                        def_ang = d_seg['start_angle']
                        def_smooth = d_seg['smooth']
                        dir_crv_idx = 0 if "Down" in d_seg['Curvature Direction'] else 1

                s_type = st.radio(f"Shape Type (S{i+1})", ["Straight Line", "Curve (Arc & Radius)", "Curve (Chord & Rise)"], index=s_type_idx, key=f"t_{i}", horizontal=True, on_change=reset_adv_state)
                
                smooth = True
                start_angle = 0.0
                if i == 0:
                    start_angle = st.number_input("Starting Angle (°)", value=float(def_ang), step=5.0, key=f"sa_{i}", on_change=reset_adv_state)
                    smooth = False
                else:
                    smooth = st.checkbox(f"Smooth Connection for S{i+1}", value=def_smooth, key=f"sm_{i}", on_change=reset_adv_state)
                    if not smooth:
                        start_angle = st.number_input(f"New Angle for S{i+1} (°)", value=float(def_ang), step=5.0, key=f"sa_{i}", on_change=reset_adv_state)

                if s_type == "Straight Line":
                    L = st.number_input(f"Length (L) (m) [S{i+1}]", value=float(def_L), step=0.5, key=f"l_{i}", on_change=reset_adv_state)
                    kappa = 0.0
                elif s_type == "Curve (Arc & Radius)":
                    r_val = st.number_input(f"Radius (R) (m) [S{i+1}]", value=float(def_R), step=0.5, key=f"r_{i}", on_change=reset_adv_state)
                    L = st.number_input(f"Arc Length (S) (m) [S{i+1}]", value=float(def_S), step=0.5, key=f"al_{i}", on_change=reset_adv_state)
                    dir_crv = st.selectbox(f"Curvature Direction [S{i+1}]", ["Arching Down ⤵ (Convex)", "Arching Up ⤴ (Concave)"], index=dir_crv_idx, key=f"d_{i}", on_change=reset_adv_state)
                    kappa = -1.0/r_val if "Down" in dir_crv else 1.0/r_val
                else:
                    L_c = st.number_input(f"Chord Length (m) [S{i+1}]", value=4.0, step=0.5, key=f"c_{i}", on_change=reset_adv_state)
                    h = st.number_input(f"Rise (m) [S{i+1}]", value=1.0, step=0.1, key=f"h_{i}", on_change=reset_adv_state)
                    if h <= 0: h = 0.01
                    r_val = (L_c**2)/(8*h) + (h/2)
                    L = 2 * r_val * np.arcsin(L_c / (2*r_val))
                    dir_crv = st.selectbox(f"Curvature Direction [S{i+1}]", ["Arching Down ⤵ (Convex)", "Arching Up ⤴ (Concave)"], key=f"d2_{i}", on_change=reset_adv_state)
                    kappa = -1.0/r_val if "Down" in dir_crv else 1.0/r_val
                    st.info(f"💡 Calculated Arc Length = {L:.2f} m | Radius = {r_val:.2f} m")
                
                segments.append({'type': s_type, 'L': L, 'kappa': kappa, 'smooth': smooth, 'start_angle': start_angle})

        st.markdown("### 2. Properties & Sections")
        sec_list = list(SECTIONS_DB.keys()) if SECTIONS_DB else ["Soldier U100"]
        def_sec_idx = next((i for i, s in enumerate(sec_list) if 'SOLDIER' in s.upper()), 0)
        
        master_sec_name = st.selectbox("Master Profile Section (Applies to all)", sec_list, index=def_sec_idx, on_change=reset_adv_state)
        master_raw = SECTIONS_DB.get(master_sec_name, {})
        master_props = {
            'name': master_sec_name, 
            'E': master_raw.get('E', 2100.0), 
            'A': master_raw.get('A', master_raw.get('A_cm2', 34.3) / 10000.0), 
            'I': master_raw.get('I', master_raw.get('I_cm4', 412.0)), 
            'Mall': master_raw.get('Mall', 13.1), 
            'Qall': master_raw.get('Qall', 100.8)
        }
        
        seg_sections = []
        with st.expander("🛠️ Override Specific Segments"):
            st.info("Check a segment below if you want to assign a different section to it.")
            for i in range(int(num_segs)):
                ovr = st.checkbox(f"Override Segment S{i+1}", key=f"ov_chk_{i}", on_change=reset_adv_state)
                if ovr:
                    ovr_name = st.selectbox(f"Section for S{i+1}", sec_list, key=f"ov_sec_{i}", on_change=reset_adv_state)
                    ovr_raw = SECTIONS_DB.get(ovr_name, {})
                    seg_sections.append({
                        'name': ovr_name, 
                        'E': ovr_raw.get('E', 2100.0), 
                        'A': ovr_raw.get('A', ovr_raw.get('A_cm2', 34.3) / 10000.0), 
                        'I': ovr_raw.get('I', ovr_raw.get('I_cm4', 412.0)), 
                        'Mall': ovr_raw.get('Mall', 13.1), 
                        'Qall': ovr_raw.get('Qall', 100.8)
                    })
                else:
                    seg_sections.append(master_props)

        st.markdown("### 3. Applied Loads")
        num_loads = st.number_input("Count of Loads", 1, 20, 1, on_change=reset_adv_state)
        loads_data = []
        seg_choices = [f"S{i+1}" for i in range(int(num_segs))]
        for i in range(int(num_loads)):
            with st.expander(f"📥 Load {i+1}", expanded=(i==0)):
                lc1, lc2, lc3 = st.columns(3)
                s_idx = lc1.selectbox("On Segment", seg_choices, key=f"ld_s_{i}", on_change=reset_adv_state)
                s_idx_num = int(s_idx[1:]) - 1
                l_type = lc2.selectbox("Type", ["Uniform", "Trapezoidal", "Point Load"], key=f"ld_t_{i}", on_change=reset_adv_state)
                l_dir = lc3.selectbox("Direction", ["Gravity (Vertical ↓)", "Perpendicular (Local ↘)"], key=f"ld_d_{i}", on_change=reset_adv_state)
                
                sc1, sc2, sc3 = st.columns(3)
                max_s = float(segments[s_idx_num]['L'])
                start = sc1.number_input("Start Arc Dist (m)", 0.0, max_s, 0.0, key=f"ld_st_{i}", on_change=reset_adv_state)
                w1 = sc2.number_input("Value W1 (kN)", value=15.0, key=f"ld_w1_{i}", on_change=reset_adv_state)
                
                if l_type == "Uniform":
                    end = sc3.number_input("End Arc Dist (m)", 0.0, max_s, max_s, key=f"ld_en_{i}", on_change=reset_adv_state)
                    w2 = w1
                elif l_type == "Trapezoidal":
                    end = sc3.number_input("End Arc Dist (m)", 0.0, max_s, max_s, key=f"ld_en_{i}", on_change=reset_adv_state)
                    w2 = st.number_input("Value W2 (kN)", value=5.0, key=f"ld_w2_{i}", on_change=reset_adv_state)
                else:
                    end = start; w2 = w1
                    
                loads_data.append({'seg_idx': s_idx_num, 'type': l_type, 'dir': l_dir, 'start': start, 'end': end, 'w1': w1, 'w2': w2})

        st.markdown("### 4. Struts (Push-Pulls)")
        def_strut_count = len(dxf_data['struts']) if dxf_data else 1
        num_struts = st.number_input("Count of Struts", 0, 20, def_strut_count, on_change=reset_adv_state)
        struts_data = []
        
        raw_struts = list(STRUTS_DB.keys()) if STRUTS_DB else ["PPH 353"]
        def strut_priority(name):
            n = name.upper()
            if "PPH" in n: return 1
            if "PPS" in n: return 2
            if "TILT" in n: return 3
            if "MMP" in n: return 4
            return 5
        strut_opts = sorted(raw_struts, key=strut_priority)
        
        master_strut = st.selectbox("Master Strut Type", strut_opts, on_change=reset_adv_state)
        
        for i in range(int(num_struts)):
            with st.expander(f"📏 Strut P{i+1}", expanded=(num_struts<3)):
                def_s_idx = 0
                def_dist = float(segments[0]['L'])/2 if segments else 1.0
                def_gx = 3.0
                if dxf_data and i < len(dxf_data['struts']):
                    ds = dxf_data['struts'][i]
                    def_s_idx = ds['seg_idx']
                    def_dist = ds['dist']
                    def_gx = ds['gx']
                    
                cc1, cc2, cc3, cc4 = st.columns(4)
                s_idx = cc1.selectbox("On Seg No.", seg_choices, index=def_s_idx, key=f"st_s_{i}", on_change=reset_adv_state)
                s_idx_num = int(s_idx[1:]) - 1
                dist = cc2.number_input("Arc Dist (m)", 0.0, float(segments[s_idx_num]['L']), value=float(def_dist), key=f"st_d_{i}", on_change=reset_adv_state)
                gx = cc3.number_input("Ground X (m)", value=float(def_gx), step=0.5, key=f"st_gx_{i}", on_change=reset_adv_state)
                st_sec = cc4.selectbox(f"P{i+1} Type", strut_opts, index=strut_opts.index(master_strut) if master_strut in strut_opts else 0, key=f"st_sec_{i}", on_change=reset_adv_state)
                struts_data.append({'seg_idx': s_idx_num, 's_dist': dist, 'gx': gx, 'sec': st_sec})

        st.markdown("### 5. Supports & Base System")
        bs1, bs2 = st.columns(2)
        base_sec_list = list(SECTIONS_DB.keys()) + ["None (Direct to Ground)"]
        base_def_idx = next((i for i, s in enumerate(base_sec_list) if 'SOLDIER' in s.upper()), 0)
        base_sec = bs1.selectbox("Base Soldier Profile", base_sec_list, index=base_def_idx, on_change=reset_adv_state)
        
        def_base_len = dxf_data['base_length'] if dxf_data else 0.0
        base_length = bs2.number_input("Total Base Length (m) [Optional]", value=float(def_base_len), step=0.5, on_change=reset_adv_state)
        
        c_sup1, c_sup2 = st.columns(2)
        c_sup = c_sup1.selectbox("Corner Support (Node 0)", ["Hinged", "Roller", "Fixed"], on_change=reset_adv_state)
        c_ang = c_sup2.number_input("Corner Angle (°)", value=0.0, step=15.0, on_change=reset_adv_state)
        
        def_supp_count = len(dxf_data['supports']) if dxf_data else 1
        num_base_sups = st.number_input("Additional Ground Supports", 0, 20, def_supp_count, on_change=reset_adv_state)
        base_sups = []
        for i in range(int(num_base_sups)):
            sp1, sp2 = st.columns(2)
            def_sx = float((i+1)*2.0)
            if dxf_data and i < len(dxf_data['supports']):
                def_sx = dxf_data['supports'][i]['x']
            sx = sp1.number_input(f"Sup G{i+1} X (m)", value=float(def_sx), on_change=reset_adv_state, key=f"sx_{i}")
            styp = sp2.selectbox(f"Sup G{i+1} Type", ["Hinged", "Roller", "Fixed"], key=f"sp_{i}", on_change=reset_adv_state)
            base_sups.append({'x': sx, 'type': styp})

    nodes, elements, nodal_loads, display_nodes, supports_list, seg_starts = build_chain_mesh(segments, seg_sections, loads_data, struts_data, base_sec, base_sups, {'type': c_sup, 'angle': c_ang}, base_length)

    with c_plot:
        st.markdown("<h3 style='text-align: center; border-bottom: 2px solid #ddd; padding-bottom: 10px; font-family: Arial; color: #1e3d59;'>Live Geometry & Assignments</h3>", unsafe_allow_html=True)
        live_img = draw_live_preview(nodes, elements, supports_list, seg_sections, loads_data, segments, seg_starts)
        st.image(live_img, use_container_width=True)

    st.markdown("---")
    
    col_btn, col_blank = st.columns([1.5, 2.5])
    with col_btn:
        if st.button("🚀 Run Advanced Chain Analysis", type="primary", use_container_width=True):
            with st.spinner("Generating Matrix & Solving..."):
                U, R = solve_fea_engine(nodes, elements, nodal_loads, supports_list)
                st.session_state.adv_fea_data = {
                    'U': U, 'R': R, 'nodes': nodes, 'elements': elements, 'display_nodes': display_nodes,
                    'supports_list': supports_list, 'seg_sections': seg_sections,
                    'loads_data': loads_data, 'segments': segments, 'seg_starts': seg_starts
                }
                st.session_state.adv_solved = True
            st.success("✅ Analysis Complete!")
            
    if st.session_state.adv_solved:
        st.markdown("### 🎛️ Analysis Results & Diagrams")
        fea_data = st.session_state.adv_fea_data
        
        with st.expander("⚙️ Diagram Scale Controls", expanded=True):
            c_s1, c_s2, c_s3 = st.columns(3)
            sc_n = c_s1.slider("Axial Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_v = c_s2.slider("Shear Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_m = c_s3.slider("Moment Scale", 0.001, 0.100, 0.015, step=0.001)
            
        img_bufs = plot_sap2000_diagrams(
            fea_data['nodes'], fea_data['elements'], fea_data['R'], 
            {'N': sc_n, 'V': sc_v, 'M': sc_m}, fea_data['display_nodes'], fea_data['supports_list'], 
            fea_data['seg_sections'], loads=fea_data['loads_data'], 
            segments=fea_data['segments'], seg_starts=fea_data['seg_starts']
        )
        
        titles = {
            'Load': "Assigned Load Diagram",
            'React': "Reactions Diagram (kN)",
            'N': "Axial Force Diagram (kN)",
            'V': "Shear Force Diagram (kN)",
            'M': "Bending Moment Diagram (kN.m)",
            'D': "Deflection Deformed Shape"
        }
        
        c_p1, c_p2, c_p3 = st.columns(3)
        c_p1.image(img_bufs['Load'], use_container_width=True)
        c_p1.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['Load']}</p>", unsafe_allow_html=True)
        c_p2.image(img_bufs['React'], use_container_width=True)
        c_p2.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['React']}</p>", unsafe_allow_html=True)
        c_p3.image(img_bufs['N'], use_container_width=True)
        c_p3.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['N']}</p>", unsafe_allow_html=True)
        
        c_p4, c_p5, c_p6 = st.columns(3)
        c_p4.image(img_bufs['V'], use_container_width=True)
        c_p4.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['V']}</p>", unsafe_allow_html=True)
        c_p5.image(img_bufs['M'], use_container_width=True)
        c_p5.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['M']}</p>", unsafe_allow_html=True)
        c_p6.image(img_bufs['D'], use_container_width=True)
        c_p6.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['D']}</p>", unsafe_allow_html=True)
        
        st.markdown("### 📊 Safety Summary")
        safety_data = []
        for i, sec in enumerate(fea_data['seg_sections']):
            max_m, max_v = 0.0, 0.0
            for el in fea_data['elements']:
                if el.get('group') == 'segment' and el.get('seg_idx') == i:
                    max_m = max(max_m, np.max(np.abs(el['internal']['M'])))
                    max_v = max(max_v, np.max(np.abs(el['internal']['V'])))
            
            s_status = "SAFE" if max_m <= sec['Mall'] and max_v <= sec['Qall'] else "UNSAFE"
            safety_data.append({
                "Component": f"S{i+1} ({get_short_name(sec['name'])})",
                "Force Type": "Bending & Shear",
                "Actual": f"M={max_m:.1f}, V={max_v:.1f}",
                "Allowable": f"M={sec['Mall']:.1f}, V={sec['Qall']:.1f}",
                "Status": s_status
            })
            
        df = pd.DataFrame(safety_data)
        st.table(df)

        fea_data['max_M'] = max([float(x['Actual'].split(',')[0].split('=')[1]) for x in safety_data])
        fea_data['max_V'] = max([float(x['Actual'].split(',')[1].split('=')[1]) for x in safety_data])
        fea_data['sec_props'] = {'name': "Mixed Sections", 'Mall': 999.0, 'Qall': 999.0}
        fea_data['img_bufs'] = img_bufs
        fea_data['safety_df'] = safety_data
        
        st.markdown("---")
        doc_out = generate_chain_report(fea_data)
        st.download_button("⬇️ Download Calculation Sheet (Word)", data=doc_out.getvalue(), file_name="Advanced_Shape_Calculation_Sheet.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

if __name__ == "__main__":
    render_advanced_shape_module()
