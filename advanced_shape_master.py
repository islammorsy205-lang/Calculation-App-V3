# advanced_shape_master.py

import streamlit as st
import numpy as np
import pandas as pd
import io
import os
import re
import math
import tempfile
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
    if abs(force_mag) < 0.001:
        return
    arr_L = 0.5  
    sgn = np.sign(force_mag)
    dx = sgn * axis_nx
    dy = sgn * axis_ny
    start_x = node_x - arr_L * dx
    start_y = node_y - arr_L * dy
    arr_c = 'blue' if force_mag >= 0 else 'red'
    
    ax.arrow(
        start_x, start_y, arr_L*dx, arr_L*dy, 
        length_includes_head=True, 
        head_width=0.08, head_length=0.12, 
        fc=arr_c, ec=arr_c, lw=0.8, zorder=5
    )
    ax.text(
        start_x - 0.15*dx, start_y - 0.15*dy, 
        f"{force_mag:+.3f}", 
        color=arr_c, fontsize=7, fontname='Arial', 
        ha='center', va='center'
    )

def point_on_line_segment(px, py, x1, y1, x2, y2, tol=1e-3):
    if not (min(x1, x2) - tol <= px <= max(x1, x2) + tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol):
        return False
    cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
    if abs(cross) > tol:
        return False
    return True

def get_closest_segment_exact(pt, segs):
    min_d = 9999.0
    best_idx = 0
    best_s = 0.0
    pt = np.array(pt)
    
    for idx, seg in enumerate(segs):
        L = seg.get('L', 0.0)
        if seg.get('Shape Type') == 'Straight Line' and 'abs_p1' in seg:
            p1 = np.array(seg['abs_p1'])
            p2 = np.array(seg['abs_p2'])
            v = p2 - p1
            w = pt - p1
            c2 = np.dot(v, v)
            if c2 > 1e-6:
                ratio = np.dot(w, v) / c2
            else:
                ratio = 0.0
            ratio = max(0.0, min(1.0, ratio))
            proj = p1 + ratio * v
            d = np.linalg.norm(pt - proj)
            if d < min_d:
                min_d = d
                best_idx = idx
                best_s = ratio * L
                
        elif seg.get('Shape Type') == 'Curve (Arc & Radius)' and 'abs_c' in seg:
            c = np.array(seg['abs_c'])
            r = seg['abs_r']
            v = pt - c
            ang = math.atan2(v[1], v[0])
            sa = seg['abs_sa']
            sweep = seg['sweep']
            ang_norm = (ang - sa) % (2 * math.pi)
            if ang_norm > abs(sweep):
                ratio = 1.0 if ang_norm < math.pi else 0.0
            else:
                ratio = ang_norm / sweep if abs(sweep) > 1e-6 else 0.0
            ratio = max(0.0, min(1.0, ratio))
            current_ang = sa + ratio * sweep
            proj = c + r * np.array([math.cos(current_ang), math.sin(current_ang)])
            d = np.linalg.norm(pt - proj)
            if d < min_d:
                min_d = d
                best_idx = idx
                best_s = ratio * L
                
    return min_d, best_idx, best_s

# =========================================================
# 1. THE SUPER DXF PARSER & AUTO-MESHER
# =========================================================
def parse_dxf_to_data(file_bytes):
    if ezdxf is None:
        return None
        
    tmp_path = ""
    try:
        try:
            dxf_str = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            dxf_str = file_bytes.decode('cp1252', errors='ignore')
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf", mode='w', encoding='utf-8') as tmp:
            tmp.write(dxf_str)
            tmp_path = tmp.name
            
        doc = ezdxf.readfile(tmp_path)
        msp = doc.modelspace()
        
        raw_frames = []
        raw_struts = []
        raw_supports = []
        
        def match_layer(layer, target):
            l_clean = layer.lower().replace(" ", "").replace("_", "")
            return target in l_clean
        
        # 1. استخراج الداتا الخام وتفجير المنحنيات
        for e in msp:
            lyr = e.dxf.layer
            etype = e.dxftype()
            
            if match_layer(lyr, "supp"):
                if etype in ['POINT', 'CIRCLE', 'INSERT']:
                    if etype == 'POINT':
                        raw_supports.append({'x': e.dxf.location.x, 'y': e.dxf.location.y})
                    elif etype == 'CIRCLE':
                        raw_supports.append({'x': e.dxf.center.x, 'y': e.dxf.center.y})
                    elif etype == 'INSERT':
                        raw_supports.append({'x': e.dxf.insert.x, 'y': e.dxf.insert.y})
            
            elif match_layer(lyr, "push") or match_layer(lyr, "pull"):
                entities = [e]
                if etype in ['LWPOLYLINE', 'POLYLINE']:
                    entities = list(e.virtual_entities())
                for sub_e in entities:
                    if sub_e.dxftype() == 'LINE':
                        raw_struts.append({
                            'p1': [sub_e.dxf.start.x, sub_e.dxf.start.y], 
                            'p2': [sub_e.dxf.end.x, sub_e.dxf.end.y]
                        })
                    
            elif match_layer(lyr, "frame"):
                entities = [e]
                if etype in ['LWPOLYLINE', 'POLYLINE']:
                    entities = list(e.virtual_entities())
                for sub_e in entities:
                    if sub_e.dxftype() == 'LINE':
                        raw_frames.append({
                            'type': 'line', 
                            'x1': sub_e.dxf.start.x, 'y1': sub_e.dxf.start.y, 
                            'x2': sub_e.dxf.end.x, 'y2': sub_e.dxf.end.y
                        })
                    elif sub_e.dxftype() == 'ARC':
                        raw_frames.append({
                            'type': 'arc', 
                            'c': [sub_e.dxf.center.x, sub_e.dxf.center.y], 
                            'r': sub_e.dxf.radius, 
                            'sa': math.radians(sub_e.dxf.start_angle), 
                            'ea': math.radians(sub_e.dxf.end_angle)
                        })

        if not raw_frames:
            return None

        # 2. محرك التقطيع الأوتوماتيكي (The Auto-Mesher)
        cut_points = [(s['x'], s['y']) for s in raw_supports]
        for st_line in raw_struts:
            cut_points.append((st_line['p1'][0], st_line['p1'][1]))
            cut_points.append((st_line['p2'][0], st_line['p2'][1]))

        meshed_lines = []
        for f in raw_frames:
            if f['type'] == 'line':
                x1, y1 = f['x1'], f['y1']
                x2, y2 = f['x2'], f['y2']
                pts_on_line = [(x1, y1), (x2, y2)]
                
                for cp in cut_points:
                    if point_on_line_segment(cp[0], cp[1], x1, y1, x2, y2):
                        pts_on_line.append(cp)
                        
                pts_on_line.sort(key=lambda p: math.hypot(p[0]-x1, p[1]-y1))
                
                unique_pts = [pts_on_line[0]]
                for p in pts_on_line[1:]:
                    if math.hypot(p[0]-unique_pts[-1][0], p[1]-unique_pts[-1][1]) > 1e-4:
                        unique_pts.append(p)
                        
                for i in range(len(unique_pts)-1):
                    meshed_lines.append({
                        'type': 'line',
                        'x1': unique_pts[i][0], 'y1': unique_pts[i][1],
                        'x2': unique_pts[i+1][0], 'y2': unique_pts[i+1][1]
                    })
            elif f['type'] == 'arc':
                meshed_lines.append(f)

        def get_min_x(f):
            if f['type'] == 'line': return min(f['x1'], f['x2'])
            return f['c'][0] - f['r']
            
        meshed_lines.sort(key=get_min_x)

        # 3. بناء الـ Segments للواجهة
        chained_segs = []
        for f in meshed_lines:
            if f['type'] == 'line':
                p_start = (f['x1'], f['y1'])
                p_end = (f['x2'], f['y2'])
                
                if p_start[0] > p_end[0] + 1e-5 or (abs(p_start[0] - p_end[0]) < 1e-5 and p_start[1] > p_end[1]):
                    p_start, p_end = p_end, p_start
                    
                dx_line = p_end[0]-p_start[0]
                dy_line = p_end[1]-p_start[1]
                L = math.hypot(dx_line, dy_line)
                ang = math.degrees(math.atan2(dy_line, dx_line))
                
                chained_segs.append({
                    'type': 'Straight Line', 'Shape Type': 'Straight Line', 'L': L, 
                    'start_angle': ang, 'smooth': False, 'is_dxf': True, 
                    'abs_p1': p_start, 'abs_p2': p_end, 'kappa': 0.0
                })
                
            elif f['type'] == 'arc':
                sa, ea = f['sa'], f['ea']
                if ea < sa: ea += 2 * math.pi
                sweep = ea - sa
                L = f['r'] * sweep
                
                chained_segs.append({
                    'type': 'Curve (Arc & Radius)', 'Shape Type': 'Curve (Arc & Radius)', 
                    'L': L, 'Radius (R) (m)': f['r'], 'Curvature Direction': "Arching Up ⤴ (Concave)",
                    'start_angle': math.degrees(sa + math.pi/2), 'smooth': False, 'is_dxf': True, 
                    'abs_c': tuple(f['c']), 'abs_r': f['r'], 'abs_sa': sa, 'abs_ea': ea, 
                    'sweep': sweep, 'kappa': 1.0/f['r']
                })

        # 4. تعيين أماكن النهايز بالظبط على الـ Segments المقطوعة
        struts_mapped = []
        for s in raw_struts:
            p1, p2 = s['p1'], s['p2']
            top_p, bot_p = (p1, p2) if p1[1] > p2[1] else (p2, p1)
            
            best_idx = 0; best_dist = 0.0; min_err = 9999.0
            
            for i, seg in enumerate(chained_segs):
                if seg['type'] == 'Straight Line':
                    s_p1, s_p2 = seg['abs_p1'], seg['abs_p2']
                    err1 = math.hypot(top_p[0]-s_p1[0], top_p[1]-s_p1[1])
                    err2 = math.hypot(top_p[0]-s_p2[0], top_p[1]-s_p2[1])
                    if err1 < min_err: min_err = err1; best_idx = i; best_dist = 0.0
                    if err2 < min_err: min_err = err2; best_idx = i; best_dist = seg['L']
            
            struts_mapped.append({
                'seg_idx': best_idx, 'dist': best_dist, 
                'gx': bot_p[0], 'gy': bot_p[1],
                'raw_top_x': top_p[0], 'raw_top_y': top_p[1]
            })

        # 5. تعيين أماكن الدعامات
        supps_mapped = []
        for sp in raw_supports:
            d_min, b_seg, b_s = get_closest_segment_exact((sp['x'], sp['y']), chained_segs)
            if d_min < 0.1:
                supps_mapped.append({'x': sp['x'], 'y': sp['y'], 'type': 'Hinged', 'seg_idx': b_seg, 's_dist': b_s})
            else:
                supps_mapped.append({'x': sp['x'], 'y': sp['y'], 'type': 'Hinged'})

        return {'segments': chained_segs, 'struts': struts_mapped, 'supports': supps_mapped}
        
    except Exception as e:
        st.error(f"⚠️ حدث خطأ أثناء تحليل ملف الـ DXF. تأكد من صحة الطبقات: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass
            # =========================================================
# 2. Geometry & Mesh Generators (Absolute Eval Engine)
# =========================================================
def eval_seg_point(seg, s_val, start_data=None):
    L = seg.get('L', 0.0)
    s_val = min(max(s_val, 0.0), L)
    
    ratio = s_val / L if L > 1e-6 else 0.0
    
    is_dxf = seg.get('is_dxf', False)
    shape_type = seg.get('Shape Type', 'Straight Line')
    
    if is_dxf:
        if shape_type == 'Straight Line' and 'abs_p1' in seg:
            p1 = seg['abs_p1']
            p2 = seg['abs_p2']
            px = p1[0] + ratio * (p2[0] - p1[0])
            py = p1[1] + ratio * (p2[1] - p1[1])
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            th = math.atan2(dy, dx)
            return px, py, th
            
        elif shape_type == 'Curve (Arc & Radius)' and 'abs_c' in seg:
            c = seg['abs_c']
            r = seg['abs_r']
            current_ang = seg['abs_sa'] + ratio * seg.get('sweep', 0)
            px = c[0] + r * math.cos(current_ang)
            py = c[1] + r * math.sin(current_ang)
            th = current_ang + math.pi/2
            return px, py, th
            
    if start_data:
        x0 = start_data.get('x0', 0)
        y0 = start_data.get('y0', 0)
        th0 = start_data.get('th0', 0)
        kappa = start_data.get('kappa', 0)
        
        if abs(kappa) < 1e-6: 
            x = x0 + s_val * math.cos(th0)
            y = y0 + s_val * math.sin(th0)
            th = th0
        else: 
            x = x0 + (math.sin(th0 + kappa * s_val) - math.sin(th0)) / kappa
            y = y0 - (math.cos(th0 + kappa * s_val) - math.cos(th0)) / kappa
            th = th0 + kappa * s_val
            
        return x, y, th
    
    return 0.0, 0.0, 0.0

def get_approx_xy(segs, s_idx, s_val):
    if s_idx < 0 or s_idx >= len(segs):
        return 0.0, 0.0
        
    seg = segs[s_idx]
    if seg.get('is_dxf'):
        px, py, _ = eval_seg_point(seg, s_val)
        return px, py
        
    curr_x, curr_y, curr_th = 0.0, 0.0, 0.0
    
    for i in range(s_idx + 1):
        sg = segs[i]
        if i == 0 or not sg.get('smooth', True):
            curr_th = math.radians(sg.get('start_angle', 0.0))
            
        L = sg.get('L', 0.0)
        kappa = sg.get('kappa', 0.0)
        
        if i == s_idx:
            if abs(kappa) < 1e-6:
                return curr_x + s_val * math.cos(curr_th), curr_y + s_val * math.sin(curr_th)
            else:
                return curr_x + (math.sin(curr_th + kappa*s_val) - math.sin(curr_th))/kappa, curr_y - (math.cos(curr_th + kappa*s_val) - math.cos(curr_th))/kappa
                
        if abs(kappa) < 1e-6:
            curr_x += L * math.cos(curr_th)
            curr_y += L * math.sin(curr_th)
        else:
            curr_x += (math.sin(curr_th + kappa*L) - math.sin(curr_th))/kappa
            curr_y -= (math.cos(curr_th + kappa*L) - math.cos(curr_th))/kappa
            curr_th += kappa * L
            
    return curr_x, curr_y

def build_chain_mesh(segments, seg_sections, loads, struts, base_sec, supports, corner_sup, mesh_size=0.25):
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
        if not seg.get('is_dxf'):
            if i == 0 or not seg.get('smooth', True):
                curr_th = np.radians(seg.get('start_angle', 0.0))
            
        L = seg.get('L', 0.0)
        kappa = seg.get('kappa', 0.0)
        seg_start_data.append({'x0': curr_x, 'y0': curr_y, 'th0': curr_th, 'kappa': kappa})
        
        key_s_vals = [0.0, L]
        for st_item in struts:
            if st_item.get('seg_idx') == i: key_s_vals.append(st_item['s_dist'])
                
        for ld in loads:
            if ld.get('seg_idx') == i:
                key_s_vals.extend([ld['start'], ld['end']])
                
        for sp in supports:
            if sp.get('seg_idx') == i: key_s_vals.append(sp['s_dist'])
            
        keys = list(key_s_vals)
        num_sub = max(1, int(np.ceil(L / mesh_size)))
        for p in np.linspace(0, L, num_sub+1): keys.append(p)
            
        keys = sorted(list(set([min(max(round(k, 5), 0.0), round(L, 5)) for k in keys])))
        
        node_indices = []
        for s_val in keys:
            px, py, _ = eval_seg_point(seg, s_val, seg_start_data[i])
            nid = get_or_add_node(px, py)
            node_indices.append(nid)
            if any(abs(s_val - kv) < 1e-4 for kv in key_s_vals): key_nodes.add(nid)
            
        sec_props = seg_sections[i]
        
        for j in range(len(keys)-1):
            n1 = node_indices[j]
            n2 = node_indices[j+1]
            if n1 == n2: continue 
                
            s_mid = (keys[j] + keys[j+1]) / 2.0
            _, _, th_mid = eval_seg_point(seg, s_mid, seg_start_data[i])
            c_t, s_t = np.cos(th_mid), np.sin(th_mid)
            p_x1, p_y1, p_x2, p_y2 = 0.0, 0.0, 0.0, 0.0
            
            for ld in loads:
                if ld.get('seg_idx') == i and ld.get('type') != 'Point Load':
                    if ld['start'] - 1e-4 <= s_mid <= ld['end'] + 1e-4:
                        L_ld = max(ld['end'] - ld['start'], 1e-5)
                        wa = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j] - ld['start']) / L_ld
                        wb = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j+1] - ld['start']) / L_ld
                        
                        dir_str = ld.get('dir', '')
                        if 'Global Z' in dir_str or 'Global Y' in dir_str:
                            p_x1 += wa * s_t; p_y1 += wa * c_t; p_x2 += wb * s_t; p_y2 += wb * c_t
                        elif 'Global X' in dir_str:
                            p_x1 += wa * c_t; p_y1 -= wa * s_t; p_x2 += wb * c_t; p_y2 -= wb * s_t
                        else:
                            p_y1 += wa; p_y2 += wb
                            
            elements.append({
                'type': 'frame', 'group': 'segment', 'sec': sec_props['name'],
                'n1': n1, 'n2': n2, 'px1': p_x1, 'py1': p_y1, 'px2': p_x2, 'py2': p_y2,
                'E': sec_props['E'] * 10000.0, 'A': sec_props['A'], 'I': sec_props['I'] / 100000000.0,
                'seg_idx': i, 's_start': keys[j], 's_end': keys[j+1], 'L': keys[j+1] - keys[j], 'th_mid': th_mid
            })
            
        for ld in loads:
            if ld.get('seg_idx') == i and ld.get('type') == 'Point Load':
                try:
                    idx = keys.index(round(ld['start'], 5))
                    nid = node_indices[idx]
                    dir_str = ld.get('dir', '')
                    if 'Global Z' in dir_str or 'Global Y' in dir_str:
                        nodal_loads.append({'node': nid, 'Fx': 0.0, 'Fy': ld['w1']})
                    elif 'Global X' in dir_str:
                        nodal_loads.append({'node': nid, 'Fx': ld['w1'], 'Fy': 0.0})
                    else: 
                        _, _, th_pt = eval_seg_point(seg, ld['start'], seg_start_data[i])
                        c_pt, s_pt = np.cos(th_pt), np.sin(th_pt)
                        nodal_loads.append({'node': nid, 'Fx': -ld['w1']*s_pt, 'Fy': ld['w1']*c_pt})
                except ValueError: pass
                
        curr_x, curr_y, curr_th = eval_seg_point(seg, L, seg_start_data[i])

    for st_idx, st_item in enumerate(struts):
        seg_idx = st_item.get('seg_idx', 0)
        dist = round(st_item.get('s_dist', 0.0), 5)
        gx, gy = st_item.get('gx', 0.0), st_item.get('gy', 0.0)
        nx, ny, _ = eval_seg_point(segments[seg_idx], dist, seg_start_data[seg_idx])
        
        top_node = get_or_add_node(nx, ny)
        bot_node = get_or_add_node(gx, gy)
        
        elements.append({
            'type': 'truss', 'group': 'strut', 'sec': st_item.get('sec', 'Unknown'),
            'n1': bot_node, 'n2': top_node, 'strut_idx': st_idx, 'E': 21000000.0, 'A': 0.001
        })

    supports_list = []
    for sup in supports:
        if 'seg_idx' in sup:
            s_dist = round(sup['s_dist'], 5)
            nx, ny, _ = eval_seg_point(segments[sup['seg_idx']], s_dist, seg_start_data[sup['seg_idx']])
            nid = get_or_add_node(nx, ny)
        else:
            nid = get_or_add_node(sup.get('x', 0.0), sup.get('y', 0.0))
            
        supports_list.append({'node': nid, 'type': sup.get('type', 'Hinged'), 'angle': sup.get('angle', 0.0)})
        
    display_nodes = set([s['node'] for s in supports_list])
    display_nodes.update(key_nodes) 

    return nodes, elements, nodal_loads, display_nodes, supports_list, seg_start_data

# =========================================================
# 3. Advanced FEA Solver (Exact Matrix Engine)
# =========================================================
def solve_fea_engine(nodes, elements, nodal_loads, supports_list):
    NDOF = len(nodes) * 3
    K = np.zeros((NDOF, NDOF))
    F = np.zeros(NDOF)
    
    for el in elements:
        n1, n2 = el['n1'], el['n2']
        x1, y1 = nodes[n1][0], nodes[n1][1]
        x2, y2 = nodes[n2][0], nodes[n2][1]
        L = np.hypot(x2 - x1, y2 - y1)
        
        if L < 1e-5: 
            el['c'], el['s'], el['L'] = 1, 0, 1e-5
            el['internal'] = {'N': [0,0], 'V': [0,0], 'M': [0,0], 'x': [0, 1e-5], 'v_rel': [0,0]}
            continue
            
        c, s = (x2 - x1) / L, (y2 - y1) / L
        el['L'], el['c'], el['s'] = L, c, s
        E, A, I = el['E'], el['A'], el.get('I', 0.00005)
        
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]
        ])
        
        if el['type'] == 'truss':
            k_loc = np.zeros((6, 6))
            k_loc[0,0] = E * A / L; k_loc[3,3] = E * A / L
            k_loc[0,3] = -E * A / L; k_loc[3,0] = -E * A / L
        else:
            k_loc = np.array([
                [E*A/L, 0, 0, -E*A/L, 0, 0], 
                [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L], 
                [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2], 
                [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/L**2, 4*E*I/L]
            ])
            
            px1, py1 = el.get('px1',0), el.get('py1',0)
            px2, py2 = el.get('px2',0), el.get('py2',0)
            
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
        n, t, a = sup['node'], sup['type'], sup.get('angle', 0.0)
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
    except np.linalg.LinAlgError: U[free_dof] = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    R_reactions = K_orig @ U - F 
    
    for el in elements:
        if el.get('L', 0) < 1e-5: continue
            
        n1, n2 = el['n1'], el['n2']
        c, s, L, E, A, I = el['c'], el['s'], el['L'], el['E'], el['A'], el.get('I', 0.00005)
        
        u_glob = U[[3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]]
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]
        ])
        
        u_loc = T @ u_glob
        el['internal'] = {'u_loc': u_loc}
        
        if el['type'] == 'truss':
            N_val = (E * A / L) * (u_loc[3] - u_loc[0])
            xs = np.linspace(0, L, 51)
            el['internal'].update({
                'N': np.full_like(xs, N_val), 'V': np.zeros_like(xs), 'M': np.zeros_like(xs),
                'x': xs, 'v_rel': np.zeros_like(xs)
            })
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
# 4. Plotting Engine (Live Preview & SAP2000 Colors)
# =========================================================
def draw_base_geometry(ax, nodes, elements, supports_list, seg_sections=None, segments=None, seg_starts=None):
    for el in elements:
        if el['type'] not in ['frame', 'truss']:
            continue
            
        n1 = nodes[el['n1']]
        n2 = nodes[el['n2']]
        
        if el['type'] == 'truss':
            ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color='red', linestyle='-', linewidth=0.8, zorder=1)
        else:
            if el.get('group') == 'base' and el.get('sec') == "None (Direct to Ground)":
                continue
                
            if el.get('group') == 'segment' and segments and seg_starts:
                s_idx = el['seg_idx']
                seg = segments[s_idx]
                s_data = seg_starts[s_idx]
                curve_x, curve_y = [], []
                s_start = el.get('s_start', 0.0)
                s_end = el.get('s_end', el.get('L', 0.0))
                
                for p in np.linspace(s_start, s_end, 10):
                    cx, cy, _ = eval_seg_point(seg, p, s_data)
                    curve_x.append(cx)
                    curve_y.append(cy)
                ax.plot(curve_x, curve_y, color='royalblue', linestyle='-', linewidth=1.5, zorder=1)
            else:
                ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color='royalblue', linestyle='-', linewidth=1.5, zorder=1)
            
    for i, sup in enumerate(supports_list):
        n = sup['node']
        x, y = nodes[n][0], nodes[n][1]
        t = sup['type']
        
        ax.text(x, y - 0.4, f"J{i+1}", color='green', fontsize=7, ha='center', fontname='Arial')
        
        if t == 'Fixed':
            ax.plot(x, y, marker='s', markerfacecolor='none', markeredgecolor='limegreen', markersize=3, zorder=5)
            ax.plot([x - 0.1, x + 0.1], [y - 0.1, y + 0.1], color='limegreen', lw=1.0, zorder=4)
        elif t == 'Hinged':
            h, w = 0.15, 0.12
            p1, p2, p3 = (x, y), (x + w, y - h), (x - w, y - h)
            ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            ax.plot([x - w - 0.05, x + w + 0.05], [y - h, y - h], color='limegreen', lw=1.0, zorder=4)
        elif t == 'Roller':
            h, w, r = 0.15, 0.12, 0.04
            p1, p2, p3 = (x, y), (x + w, y - h), (x - w, y - h)
            ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            ax.add_patch(plt.Circle((x, y - h - r), r, facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            ax.plot([x - w - 0.05, x + w + 0.05], [y - h - 2*r, y - h - 2*r], color='limegreen', lw=1.0, zorder=4)

    if seg_sections and segments and seg_starts:
        for el in elements:
            if el['type'] == 'truss':
                n1, n2 = nodes[el['n1']], nodes[el['n2']]
                mid_x, mid_y = (n1[0]+n2[0])/2, (n1[1]+n2[1])/2
                dx, dy = n2[0]-n1[0], n2[1]-n1[1]
                rot = np.degrees(math.atan2(dy, dx))
                if rot > 90: rot -= 180
                elif rot < -90: rot += 180
                L_hyp = np.hypot(dx, dy)
                if L_hyp > 1e-4:
                    nx_s, ny_s = -dy/L_hyp, dx/L_hyp
                    st_id = el.get('strut_idx', 0) + 1
                    label = f"P{st_id}: {get_short_name(el.get('sec', ''))}"
                    ax.text(mid_x + nx_s*0.1, mid_y + ny_s*0.1, label, color='dimgray', fontsize=6, rotation=rot, ha='center', va='center', fontname='Arial')
        
        for i, seg in enumerate(segments):
            s_data = seg_starts[i]
            mx, my, mth = eval_seg_point(seg, seg.get('L', 0)/2, s_data)
            rot_deg = math.degrees(mth)
            if rot_deg > 90: rot_deg -= 180
            elif rot_deg < -90: rot_deg += 180
            sec_name = seg_sections[i]['name']
            label = f"S{i+1}: {get_short_name(sec_name)}"
            ax.text(mx - math.sin(mth)*0.1, my + math.cos(mth)*0.1, label, color='dimgray', fontsize=6, ha='center', va='center', rotation=rot_deg, fontname='Arial')

def draw_loads_and_geometry(ax, nodes, elements, supports_list, seg_sections, loads, segments=None, seg_starts=None):
    draw_base_geometry(ax, nodes, elements, supports_list, seg_sections, segments, seg_starts)
    scale_ld = 0.05
    for ld in loads:
        if segments and seg_starts:
            i = ld.get('seg_idx', 0)
            s_data = seg_starts[i]
            w1, w2 = ld.get('w1', 0), ld.get('w2', 0)
            num_pts = max(10, int((ld.get('end', 0) - ld.get('start', 0)) / 0.1))
            s_vals = np.linspace(ld.get('start', 0), ld.get('end', 0), num_pts)
            poly_pts, top_pts = [], []
            
            for sv in s_vals:
                px, py, th = eval_seg_point(segments[i], sv, s_data)
                w_curr = w1 + (w2 - w1) * (sv - ld.get('start', 0)) / max(1e-5, (ld.get('end', 0) - ld.get('start', 0)))
                w_val = w_curr * scale_ld
                poly_pts.append((px, py))
                
                dir_str = ld.get('dir', '')
                if 'Global Z' in dir_str or 'Global Y' in dir_str:
                    f_vx, f_vy = 0.0, w_val
                elif 'Global X' in dir_str:
                    f_vx, f_vy = w_val, 0.0
                else:
                    c, s = math.cos(th), math.sin(th)
                    f_vx, f_vy = -s * w_val, c * w_val
                top_pts.append((px - f_vx, py - f_vy))
                    
            poly_pts.extend(top_pts[::-1])
            if len(poly_pts) > 2:
                ax.add_patch(Polygon(poly_pts, facecolor='none', edgecolor='blue', lw=0.8, zorder=2))

def draw_live_preview(nodes, elements, supports_list, seg_sections, loads, segments=None, seg_starts=None):
    apply_plot_styles()
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    draw_loads_and_geometry(ax, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts)
    return safe_render_fig(fig)

def plot_sap2000_diagrams(nodes, elements, R_reactions, scales, display_nodes, supports_list, seg_sections, loads, segments=None, seg_starts=None):
    apply_plot_styles()
    figs_dict = {}
    
    fig_ld, ax_ld = plt.subplots(figsize=(6, 5))
    ax_ld.set_aspect('equal', adjustable='datalim'); ax_ld.axis('off')
    draw_loads_and_geometry(ax_ld, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts)
    figs_dict['Load'] = safe_render_fig(fig_ld)
    
    fig_r, ax_r = plt.subplots(figsize=(6, 5))
    ax_r.set_aspect('equal', adjustable='datalim'); ax_r.axis('off')
    draw_base_geometry(ax_r, nodes, elements, supports_list, seg_sections, segments, seg_starts)
    
    for sup in supports_list:
        n = sup['node']
        t = sup['type']
        Rx, Ry = R_reactions[3*n], R_reactions[3*n+1]
        x, y = nodes[n][0], nodes[n][1]
        if t == 'Roller': draw_reaction_arrow(ax_r, x, y, Ry, 0, 1)
        else:
            draw_reaction_arrow(ax_r, x, y, Rx, 1, 0)
            draw_reaction_arrow(ax_r, x, y, Ry, 0, 1)
    figs_dict['React'] = safe_render_fig(fig_r)
    
    def create_force_plot(val_key, scale, c_pos, c_neg):
        fig_f, ax_f = plt.subplots(figsize=(6, 5))
        ax_f.set_aspect('equal', adjustable='datalim'); ax_f.axis('off')
        draw_base_geometry(ax_f, nodes, elements, supports_list, seg_sections, segments, seg_starts)
        global_texts = []
        
        def is_far(tx, ty):
            for (px, py) in global_texts:
                if math.hypot(tx-px, ty-py) < 0.35: return False
            return True

        for el in elements:
            n1, n2 = el['n1'], el['n2']
            x1, y1 = nodes[n1][0], nodes[n1][1]
            x2, y2 = nodes[n2][0], nodes[n2][1]
            c, s = el.get('c', 1), el.get('s', 0)
            xs = el.get('internal', {}).get('x', [])
            vals = el.get('internal', {}).get(val_key, [])
            if len(vals) == 0 or np.all(np.abs(vals) < 1e-4): continue
            
            plot_vals = -vals if val_key != 'N' else vals
            px = x1 + c * xs - s * plot_vals * scale
            py = y1 + s * xs + c * plot_vals * scale
            
            for k in range(len(px)-1):
                color = c_pos if vals[k] >= 0 else c_neg
                ax_f.plot([px[k], px[k+1]], [py[k], py[k+1]], color=color, lw=0.8)
                
            ax_f.plot([x1, px[0]], [y1, py[0]], color=c_pos if vals[0]>=0 else c_neg, lw=0.8)
            ax_f.plot([x2, px[-1]], [y2, py[-1]], color=c_pos if vals[-1]>=0 else c_neg, lw=0.8)

            def plot_val(idx):
                v = vals[idx]
                if abs(v) < 0.1: return
                tx, ty = px[idx], py[idx]
                sgn = 1 if plot_vals[idx] >= 0 else -1
                tx += -s * sgn * 0.15; ty += c * sgn * 0.15
                v_color = c_pos if v >= 0 else c_neg
                if is_far(tx, ty):
                    ax_f.text(tx, ty, f"{v:+.1f}", fontsize=6, color=v_color, ha='center', va='center', fontname='Arial')
                    global_texts.append((tx, ty))
            if len(vals) > 0: plot_val(len(vals)//2)
        return safe_render_fig(fig_f)

    figs_dict['N'] = create_force_plot('N', scales['N'], 'blue', 'red')
    figs_dict['V'] = create_force_plot('V', scales['V'], 'blue', 'red')
    figs_dict['M'] = create_force_plot('M', scales['M'], 'blue', 'red')
    
    fig_d, ax_d = plt.subplots(figsize=(6, 5))
    ax_d.set_aspect('equal', adjustable='datalim'); ax_d.axis('off')
    draw_base_geometry(ax_d, nodes, elements, supports_list, seg_sections, segments, seg_starts)
    
    max_def = 0.0
    for el in elements:
        if el['type'] == 'frame':
            xs = el.get('internal', {}).get('x', [])
            v_rel = el.get('internal', {}).get('v_rel', np.zeros_like(xs)) * 20.0 
            if len(xs) == 0: continue
            x1, y1 = nodes[el['n1']][0], nodes[el['n1']][1]
            c, s = el.get('c', 1), el.get('s', 0)
            px = x1 + c * xs - s * v_rel; py = y1 + s * xs + c * v_rel
            ax_d.plot(px, py, color='red', linestyle='--', linewidth=1.2, alpha=0.8)
            max_def = max(max_def, np.max(np.abs(el.get('internal', {}).get('v_rel', [0]))))
            
    if max_def > 0:
        ax_d.text(nodes[0][0], nodes[0][1]+1.0, f"Max Deflection = {max_def*1000:.2f} mm", color='red', fontsize=10, fontweight='bold')
    figs_dict['D'] = safe_render_fig(fig_d)
    return figs_dict

# =========================================================
# 5. Word Report Generator
# =========================================================
def generate_chain_report(sys_data):
    doc = Document("Acrow_Template.docx") if os.path.exists("Acrow_Template.docx") else Document()
    
    def force_ltr_left(p):
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pPr = p._element.get_or_add_pPr()
        bidi = OxmlElement('w:bidi'); bidi.set(qn('w:val'), '0'); pPr.append(bidi)
        
    def add_line(text, bold=False):
        p = doc.add_paragraph()
        force_ltr_left(p); p.paragraph_format.line_spacing = 1.5
        r = p.add_run(text)
        r.font.name = 'Arial'; r.font.size = Pt(12); r.font.bold = bold; r.font.rtl = False
        
    p_title = doc.add_paragraph(); force_ltr_left(p_title)
    run_title = p_title.add_run("CALCULATION SHEET FOR ADVANCED SHAPES")
    run_title.font.name = 'Arial'; run_title.font.size = Pt(16); run_title.font.bold = True; run_title.font.rtl = False
    
    add_line("="*50, bold=True)
    add_line(f"1. Safety Checks:", bold=True)
    for df_row in sys_data['safety_df']:
        add_line(f"- {df_row['Component']} ({df_row['Force Type']}): {df_row['Actual']} vs {df_row['Allowable']} => {df_row['Status']}")
    
    doc.add_page_break()
    add_line("2. Analysis Diagrams:", bold=True)
    doc.add_paragraph()
    
    def add_diagram_pair(doc, img1_bytes, title1, img2_bytes, title2):
        table = doc.add_table(rows=2, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        for idx, (img_b, t_str) in enumerate([(img1_bytes, title1), (img2_bytes, title2)]):
            r_idx, c_idx = 0, idx
            p1 = table.rows[r_idx].cells[c_idx].paragraphs[0]
            p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p1.add_run().add_picture(io.BytesIO(img_b), width=Cm(8.0))
            
            p2 = table.rows[1].cells[c_idx].paragraphs[0]
            p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r3 = p2.add_run(t_str)
            r3.font.name = 'Arial'; r3.font.size = Pt(10); r3.bold = True

    bufs = sys_data['img_bufs']
    add_diagram_pair(doc, bufs['Load'], "Assigned Load Diagram", bufs['React'], "Reactions Diagram (kN)")
    doc.add_paragraph()
    add_diagram_pair(doc, bufs['N'], "Axial Force Diagram (kN)", bufs['V'], "Shear Force Diagram (kN)")
    doc.add_paragraph()
    add_diagram_pair(doc, bufs['M'], "Bending Moment Diagram (kN.m)", bufs['D'], "Deflection Shape")
    
    out = io.BytesIO(); doc.save(out)
    return out

# =========================================================
# 6. Main Streamlit UI (Classic Manual Input V3 with Origin Control)
# =========================================================
def render_advanced_shape_module():
    st.markdown("## 🎢 The Chain Builder (Classic Manual Input V3 - Full Control)")
    
    view_plane = st.radio("📐 Structural Analysis Plane / System Projection", ["Section View (XZ Axes - Vertical)", "Plan View (XY Axes - Horizontal)"], horizontal=True)
    
    c_upload, c_mesh = st.columns([2, 1])
    with c_upload:
        uploaded_dxf = st.file_uploader("📥 Upload DXF File (.dxf)", type=['dxf'], key="dxf_uploader")
    with c_mesh:
        st.write(""); st.write("")
        auto_mesh_size = st.number_input("Auto Frame Mesh Size (m)", min_value=0.05, max_value=5.0, value=0.25, step=0.05)

    if uploaded_dxf and st.button("Extract Data from DXF"):
        st.session_state.dxf_parsed = parse_dxf_to_data(uploaded_dxf.getvalue())
        if st.session_state.dxf_parsed:
            st.success("✅ DXF Parsed & Auto-Meshed Successfully with 3 Decimals Precision!")
        else: 
            st.error("❌ Failed to parse DXF.")

    dxf_data = st.session_state.get('dxf_parsed', None)

    # 📍 إحداثي نقطة الأصل (0,0) للتنظيم الدقيق
    st.markdown("### 📍 Coordinate Origin (0,0) Reference")
    col_orig1, col_orig2 = st.columns(2)
    origin_x = col_orig1.number_input("Origin X Coordinate (m)", value=0.000, format="%.3f", step=0.1)
    origin_y = col_orig2.number_input("Origin Y Coordinate (m)", value=0.000, format="%.3f", step=0.1)

    c_in, c_plot = st.columns([1.2, 1.8])
    with c_in:
        st.markdown("### 1. Supports & Base System")
        def_supp_count = len(dxf_data['supports']) if dxf_data else 2
        num_base_sups = st.number_input("Count of Point Supports", 0, 50, def_supp_count)
        
        base_sups = []
        for i in range(int(num_base_sups)):
            sp1, sp2, sp3 = st.columns([1.5, 1.5, 1])
            def_sx = float(i*2.0)
            def_type = "Hinged"
            
            if dxf_data and i < len(dxf_data['supports']):
                def_sx = dxf_data['supports'][i].get('x', 0.0)
                def_type = dxf_data['supports'][i].get('type', 'Hinged')
                
            # إحداثي الدعامة مقاس من نقطة الأصل المحددة (يمين بالموجب، شمال بالسالب)
            rel_sx = def_sx - origin_x
            sx = sp1.number_input(f"Sup J{i+1} X (rel to 0,0)", value=float(rel_sx), format="%.3f", step=0.1, key=f"sx_{i}")
            abs_sx = origin_x + sx # إحداثي مطلق للرسم والحسابات
            
            type_opts = ["Hinged", "Roller", "Fixed"]
            idx_type = type_opts.index(def_type) if def_type in type_opts else 0
            styp = sp2.selectbox(f"Sup J{i+1} Type", type_opts, index=idx_type, key=f"sp_{i}")
            
            ang = sp3.number_input(f"Angle (°)", value=0.0, step=15.0, key=f"sang_{i}") if styp == "Roller" else 0.0
            base_sups.append({'x': abs_sx, 'y': origin_y, 'type': styp, 'angle': ang})

        st.markdown("### 2. Segments (Frames)")
        num_segs = st.number_input("Number of Segments", min_value=1, max_value=100, value=len(dxf_data['segments']) if dxf_data else 1)
        seg_choices = [f"S{i+1}" for i in range(int(num_segs))]
        segments = []
        
        for i in range(int(num_segs)):
            with st.expander(f"⚙️ Segment S{i+1}", expanded=(num_segs<3)):
                s_type_idx, def_L, def_ang, def_R, def_S, def_smooth, is_dxf = 0, 3.0, 60.0, 5.0, 3.0, True, False
                abs_p1, abs_p2, abs_c, abs_r, abs_sa, abs_ea, sweep = None, None, None, None, None, None, None
                
                if dxf_data and i < len(dxf_data['segments']):
                    d_seg = dxf_data['segments'][i]
                    is_dxf = d_seg.get('is_dxf', False)
                    s_type_raw = d_seg.get('Shape Type', d_seg.get('type', 'Straight Line'))
                    if s_type_raw == 'Straight Line':
                        s_type_idx = 0; def_L = d_seg.get('L', 3.0); def_ang = d_seg.get('start_angle', 60.0)
                        abs_p1, abs_p2 = d_seg.get('abs_p1'), d_seg.get('abs_p2')
                    elif s_type_raw == 'Curve (Arc & Radius)':
                        s_type_idx = 1; def_R = d_seg.get('Radius (R) (m)', 5.0); def_S = d_seg.get('L', 3.0)
                        abs_c, abs_r, abs_sa, abs_ea, sweep = d_seg.get('abs_c'), d_seg.get('abs_r'), d_seg.get('abs_sa'), d_seg.get('abs_ea'), d_seg.get('sweep')
                        
                if is_dxf:
                    st.success(f"🔒 Mapped CAD Element: {s_type_raw} | Length: {def_L:.3f} m")
                    s_type, L, kappa, smooth, start_angle = s_type_raw, def_L, d_seg.get('kappa', 0.0), False, def_ang
                else:
                    s_type = st.radio(f"Shape Type (S{i+1})", ["Straight Line", "Curve (Arc)"], index=s_type_idx, key=f"t_{i}", horizontal=True)
                    smooth, start_angle = True, 0.0
                    if i == 0: 
                        start_angle = st.number_input("Starting Angle (°)", value=float(def_ang), format="%.3f", key=f"sa_{i}")
                        smooth = False
                    else:
                        smooth = st.checkbox(f"Smooth connection S{i+1}", value=def_smooth, key=f"sm_{i}")
                        if not smooth: start_angle = st.number_input(f"New Angle S{i+1} (°)", value=float(def_ang), format="%.3f", key=f"sa_{i}")
                            
                    if s_type == "Straight Line": 
                        L = st.number_input(f"Length L (m)", value=float(def_L), format="%.3f", step=0.1, key=f"l_{i}")
                        kappa = 0.0
                    else:
                        r_val = st.number_input(f"Radius R (m)", value=float(def_R), format="%.3f", step=0.5, key=f"r_{i}")
                        L = st.number_input(f"Arc Length (m)", value=float(def_S), format="%.3f", step=0.1, key=f"al_{i}")
                        kappa = 1.0/r_val
                
                seg_info = {'type': s_type, 'Shape Type': s_type, 'L': L, 'kappa': kappa, 'smooth': smooth, 'start_angle': start_angle}
                if is_dxf:
                    seg_info['is_dxf'] = True
                    if s_type == "Straight Line": seg_info['abs_p1'], seg_info['abs_p2'] = abs_p1, abs_p2
                    else: seg_info['abs_c'], seg_info['abs_r'], seg_info['abs_sa'], seg_info['abs_ea'], seg_info['sweep'] = abs_c, abs_r, abs_sa, abs_ea, sweep
                segments.append(seg_info)

        st.markdown("### 3. Properties")
        sec_list = list(SECTIONS_DB.keys()) if SECTIONS_DB else ["Soldier U100"]
        master_sec_name = st.selectbox("Master Section (Applies to all frames)", sec_list)
        master_raw = SECTIONS_DB.get(master_sec_name, {})
        master_props = {
            'name': master_sec_name, 'E': master_raw.get('E', 2100.0), 
            'A': master_raw.get('A', master_raw.get('A_cm2', 34.3) / 10000.0), 
            'I': master_raw.get('I', master_raw.get('I_cm4', 412.0)), 
            'Mall': master_raw.get('Mall', 13.1), 'Qall': master_raw.get('Qall', 100.8)
        }
        seg_sections = [master_props for _ in range(int(num_segs))]

        st.markdown("### 4. Applied Loads (Dead & Live & Wind)")
        cc_d, cc_l, cc_w = st.columns(3)
        fac_d = cc_d.number_input("Dead Factor", value=1.2, step=0.1, format="%.2f", key="cmb_d")
        fac_l = cc_l.number_input("Live Factor", value=1.6, step=0.1, format="%.2f", key="cmb_l")
        fac_w = cc_w.number_input("Wind Factor", value=0.0, step=0.1, format="%.2f", key="cmb_w")
        combo_factors = {'Dead Load': fac_d, 'Live Load': fac_l, 'Wind Load': fac_w}
        
        num_loads = st.number_input("Count of Loads", 0, 50, 1)
        combined_loads = []
        dir_options = ["Global Z (Vertical)", "Global X (Horizontal)", "Local Z (Perpendicular)"]
        
        for i in range(int(num_loads)):
            with st.expander(f"📥 Load Item {i+1}", expanded=(i==0)):
                col_l1, col_l2, col_l3 = st.columns(3)
                load_category = col_l1.selectbox("Load Category", ["Dead Load", "Live Load", "Wind Load"], key=f"ld_cat_{i}")
                l_type = col_l2.selectbox("Type", ["Uniform", "Trapezoidal", "Point Load"], key=f"ld_t_{i}")
                l_dir = col_l3.selectbox("Direction", dir_options, key=f"ld_d_{i}")
                
                s_choice = st.selectbox("Select Segment", seg_choices, key=f"ld_single_{i}")
                s_idx_num = int(s_choice[1:]) - 1
                max_s = float(segments[s_idx_num].get('L', 0.0))
                
                sc1, sc2 = st.columns(2)
                w1 = sc1.number_input("Value W1 (kN/m or kN)", value=-15.0, format="%.3f", key=f"ld_w1_{i}")
                w2_val = sc2.number_input("Value W2 (kN/m)", value=-5.0, format="%.3f", key=f"ld_w2_{i}") if l_type == "Trapezoidal" else w1
                
                factored_w1 = w1 * combo_factors[load_category]
                factored_w2 = w2_val * combo_factors[load_category]
                
                combined_loads.append({
                    'seg_idx': s_idx_num, 'category': load_category, 'type': l_type, 'dir': l_dir, 
                    'start': 0.0, 'end': max_s if l_type != 'Point Load' else 0.0, 'w1': factored_w1, 'w2': factored_w2
                })

        st.markdown("### 5. Struts (Push-Pulls & Ties - Dynamic Position Control)")
        num_struts = st.number_input("Count of Struts", 0, 50, len(dxf_data['struts']) if dxf_data else 0)
        struts_data = []
        strut_opts = list(STRUTS_DB.keys()) if STRUTS_DB else ["PPH 353 (1.5:3.5m)"]
        
        for i in range(int(num_struts)):
            with st.expander(f"📏 Strut P{i+1} Control Table", expanded=(num_struts<3)):
                def_s_idx = 0
                def_dist = float(segments[0].get('L', 0.0))/2 if segments else 1.0
                def_gx = origin_x + 3.0
                def_gy = origin_y
                
                if dxf_data and i < len(dxf_data['struts']):
                    ds = dxf_data['struts'][i]
                    def_s_idx = ds.get('seg_idx', 0)
                    def_dist = ds.get('dist', 1.0)
                    def_gx = ds.get('gx', def_gx)
                    
                cc1, cc2, cc3, cc4 = st.columns(4)
                s_idx_num = min(def_s_idx, len(seg_choices) - 1)
                s_idx = cc1.selectbox("Snap to Seg No.", seg_choices, index=s_idx_num, key=f"st_s_{i}")
                selected_idx = int(s_idx[1:]) - 1
                
                max_s_strut = float(segments[selected_idx].get('L', 0.0))
                # التحكم في مسافة تحريك الناهز من بداية أو نهاية الـ Frame بدقة 3 أرقام عشرية
                dist = cc2.number_input("Move from Start (m)", 0.0, max_s_strut, value=min(def_dist, max_s_strut), format="%.3f", step=0.1, key=f"st_d_{i}")
                
                # إحداثي نقطة القاعدة مقاس من (0,0) الأصلية
                rel_gx = def_gx - origin_x
                gx_rel = cc3.number_input("Base X (rel to 0,0)", value=float(rel_gx), format="%.3f", step=0.1, key=f"st_gx_{i}")
                abs_gx = origin_x + gx_rel
                
                nx, ny = get_approx_xy(segments, selected_idx, dist)
                actual_L = math.hypot(abs_gx - nx, origin_y - ny)
                
                st_sec = cc4.selectbox(f"Type (L={actual_L:.2f}m)", strut_opts, key=f"st_sec_{i}")
                
                struts_data.append({
                    'seg_idx': selected_idx, 's_dist': dist, 'gx': abs_gx, 'gy': origin_y, 'sec': st_sec
                })

    nodes, elements, nodal_loads, display_nodes, supports_list, seg_starts = build_chain_mesh(
        segments, seg_sections, combined_loads, struts_data, None, base_sups, {'type': 'Hinged', 'angle': 0.0}, mesh_size=auto_mesh_size
    )

    with c_plot:
        st.markdown("<h3 style='text-align: center; border-bottom: 2px solid #ddd; padding-bottom: 10px; font-family: Arial; color: #1e3d59;'>Live Geometry & Load Assignments</h3>", unsafe_allow_html=True)
        live_img = draw_live_preview(nodes, elements, supports_list, seg_sections, combined_loads, segments, seg_starts)
        st.image(live_img, use_container_width=True)

    if st.button("🚀 Run Analysis & Generate Diagrams", type="primary"):
        with st.spinner("Solving Finite Element Matrix..."):
            U, R = solve_fea_engine(nodes, elements, nodal_loads, supports_list)
            st.session_state.adv_fea_data = {
                'U': U, 'R': R, 'nodes': nodes, 'elements': elements, 'display_nodes': display_nodes,
                'supports_list': supports_list, 'seg_sections': seg_sections, 'loads_data': combined_loads, 
                'segments': segments, 'seg_starts': seg_starts
            }
            st.session_state.adv_solved = True
        st.success("✅ Analysis Complete!")

    if getattr(st.session_state, 'adv_solved', False):
        st.markdown("---")
        st.markdown("### 🎛️ Analysis Results & Diagrams")
        fea_data = st.session_state.adv_fea_data
        
        with st.expander("⚙️ Diagram Scale Controls", expanded=True):
            c_s1, c_s2, c_s3 = st.columns(3)
            sc_n = c_s1.slider("Axial Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_v = c_s2.slider("Shear Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_m = c_s3.slider("Moment Scale", 0.001, 0.100, 0.015, step=0.001)
            
        img_bufs = plot_sap2000_diagrams(
            fea_data['nodes'], fea_data['elements'], fea_data['R'], {'N': sc_n, 'V': sc_v, 'M': sc_m}, 
            fea_data['display_nodes'], fea_data['supports_list'], fea_data['seg_sections'], 
            loads=fea_data['loads_data'], segments=fea_data.get('segments'), seg_starts=fea_data.get('seg_starts')
        )

        titles = {'React': "Reactions (kN)", 'N': "Axial (kN)", 'V': "Shear (kN)", 'M': "Moment (kN.m)", 'D': "Deflection"}
        c_p1, c_p2, c_p3 = st.columns(3)
        c_p1.image(img_bufs['React'], use_container_width=True); c_p1.markdown(f"<p align='center'>{titles['React']}</p>", unsafe_allow_html=True)
        c_p2.image(img_bufs['V'], use_container_width=True); c_p2.markdown(f"<p align='center'>{titles['V']}</p>", unsafe_allow_html=True)
        c_p3.image(img_bufs['M'], use_container_width=True); c_p3.markdown(f"<p align='center'>{titles['M']}</p>", unsafe_allow_html=True)
        
        c_p4, c_p5 = st.columns(2)
        c_p4.image(img_bufs['N'], use_container_width=True); c_p4.markdown(f"<p align='center'>{titles['N']}</p>", unsafe_allow_html=True)
        c_p5.image(img_bufs['D'], use_container_width=True); c_p5.markdown(f"<p align='center'>{titles['D']}</p>", unsafe_allow_html=True)
        
        st.markdown("### 📊 Safety Summary")
        safety_data = []
        for i, sec in enumerate(fea_data['seg_sections']):
            max_m, max_v = 0.0, 0.0
            for el in fea_data['elements']:
                if el.get('group') == 'segment' and el.get('seg_idx') == i:
                    max_m = max(max_m, np.max(np.abs(el.get('internal', {}).get('M', [0]))))
                    max_v = max(max_v, np.max(np.abs(el.get('internal', {}).get('V', [0]))))
            s_status = "SAFE" if max_m <= sec['Mall'] and max_v <= sec['Qall'] else "UNSAFE"
            safety_data.append({
                "Component": f"S{i+1} ({get_short_name(sec['name'])})", "Force Type": "Bending & Shear", 
                "Actual": f"M={max_m:.1f}, V={max_v:.1f}", "Allowable": f"M={sec['Mall']:.1f}, V={sec['Qall']:.1f}", "Status": s_status
            })
            
        st.table(pd.DataFrame(safety_data))
        fea_data['safety_df'] = safety_data; fea_data['img_bufs'] = img_bufs
        
        doc_out = generate_chain_report(fea_data)
        st.download_button("⬇️ Download Calculation Sheet (Word)", data=doc_out.getvalue(), file_name="Advanced_Shape_Calculation_Sheet.docx")

if __name__ == "__main__":
    render_advanced_shape_module()