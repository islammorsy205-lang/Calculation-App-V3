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
    # يتم استدعاء قواعد البيانات من ملف config.py لتخفيف حجم هذا الملف
    from config import SECTIONS_DB, STRUTS_DB
    from report_builder import insert_blue_banner, add_eq, append_pdf_stream_to_word
except ImportError:
    st.error("⚠️ برجاء التأكد من وجود ملفات config.py و report_builder.py في نفس المجلد.")

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
# 1. DXF Parsing Engine (Absolute DXF Origin 0,0)
# =========================================================
def parse_dxf_to_data(file_bytes):
    if ezdxf is None: return None
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
        
        raw_frames, raw_struts, raw_supports = [], [], []
        
        def match_layer(layer, target):
            l_clean = layer.lower().replace(" ", "").replace("_", "")
            return target in l_clean
        
        for e in msp:
            lyr = e.dxf.layer
            etype = e.dxftype()
            
            # قراءة الدعامات
            if match_layer(lyr, "supp"):
                if etype in ['POINT', 'CIRCLE', 'INSERT']:
                    if etype == 'POINT':
                        raw_supports.append({'x': e.dxf.location.x, 'y': e.dxf.location.y})
                    elif etype == 'CIRCLE':
                        raw_supports.append({'x': e.dxf.center.x, 'y': e.dxf.center.y})
                    elif etype == 'INSERT':
                        raw_supports.append({'x': e.dxf.insert.x, 'y': e.dxf.insert.y})
            
            # قراءة النهايز
            elif match_layer(lyr, "push") or match_layer(lyr, "pull"):
                entities = [e]
                if etype in ['LWPOLYLINE', 'POLYLINE']:
                    entities = list(e.virtual_entities())
                for sub_e in entities:
                    if sub_e.dxftype() == 'LINE':
                        raw_struts.append({'p1': (sub_e.dxf.start.x, sub_e.dxf.start.y), 'p2': (sub_e.dxf.end.x, sub_e.dxf.end.y)})
                    
            # قراءة الفريمات (السولجر)
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
                        raw_frames.append({'type': 'arc', 'c': (c.x, c.y), 'r': r, 'sa': sa, 'ea': ea})

        if not raw_frames: return None
        
        # ترتيب الفريمات برمجياً من اليسار لليمين بدون التأثير على الإحداثيات
        def get_min_x(f):
            if f['type'] == 'line': return min(f['p1'][0], f['p2'][0])
            return f['c'][0] - f['r']
        raw_frames.sort(key=get_min_x)

        chained_segs = []
        for f in raw_frames:
            if f['type'] == 'line':
                p_start, p_end = f['p1'], f['p2']
                # توحيد اتجاهات المحاور المحلية لضمان تماثل الـ Moments
                if p_start[0] > p_end[0] + 1e-5 or (abs(p_start[0] - p_end[0]) < 1e-5 and p_start[1] > p_end[1]):
                    p_start, p_end = p_end, p_start
                    
                dx_line, dy_line = p_end[0]-p_start[0], p_end[1]-p_start[1]
                L = math.hypot(dx_line, dy_line)
                ang = math.degrees(math.atan2(dy_line, dx_line))
                chained_segs.append({
                    'type': 'Straight Line', 'Shape Type': 'Straight Line', 
                    'L': L, 'start_angle': ang, 'smooth': False, 'is_dxf': True, 
                    'abs_p1': p_start, 'abs_p2': p_end, 'kappa': 0.0
                })
            elif f['type'] == 'arc':
                sa, ea = f['sa'], f['ea']
                if ea < sa: ea += 2 * math.pi
                sweep = ea - sa
                L = f['r'] * sweep
                chained_segs.append({
                    'type': 'Curve (Arc & Radius)', 'Shape Type': 'Curve (Arc & Radius)', 
                    'L': L, 'Radius (R) (m)': f['r'],
                    'Curvature Direction': "Arching Up ⤴ (Concave)",
                    'start_angle': math.degrees(sa + math.pi/2), 'smooth': False, 'is_dxf': True, 
                    'abs_c': f['c'], 'abs_r': f['r'],
                    'abs_sa': sa, 'abs_ea': ea, 'sweep': sweep, 'kappa': 1.0/f['r']
                })

        # خوارزمية الربط الرياضي المتجهي (Vector Projection) لتحديد نقاط التقطيع
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
                    ratio = np.dot(w, v) / c2 if c2 > 1e-6 else 0.0
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

        struts_mapped = []
        for s in raw_struts:
            p1, p2 = s['p1'], s['p2']
            if p1[1] > p2[1]: top_p, bot_p = p1, p2
            else: top_p, bot_p = p2, p1
                
            d_top, b_seg, b_s = get_closest_segment_exact(top_p, chained_segs)
            struts_mapped.append({
                'seg_idx': b_seg, 'dist': b_s, 
                'gx': bot_p[0], 'gy': bot_p[1]
            })

        supps_mapped = []
        for sp in raw_supports:
            d_min, b_seg, b_s = get_closest_segment_exact((sp['x'], sp['y']), chained_segs)
            # دقة صارمة لتحديد التلامس مع القطاع
            if d_min < 0.1:
                supps_mapped.append({'x': sp['x'], 'y': sp['y'], 'type': 'Hinged', 'seg_idx': b_seg, 's_dist': b_s})
            else:
                supps_mapped.append({'x': sp['x'], 'y': sp['y'], 'type': 'Hinged'})

        return {'segments': chained_segs, 'struts': struts_mapped, 'supports': supps_mapped}
        
    except Exception as e:
        st.error(f"⚠️ حدث خطأ أثناء تحليل ملف الـ DXF. تأكد من أن الملف سليم: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

# =========================================================
# 2. Geometry & Mesh Generators (Auto-Meshing Engine)
# =========================================================
def eval_seg_point(seg, s_val, start_data=None):
    L = seg.get('L', 0.0)
    s_val = min(max(s_val, 0.0), L)
    ratio = s_val / L if L > 1e-6 else 0.0
    
    is_dxf = seg.get('is_dxf', False)
    shape_type = seg.get('Shape Type', 'Straight Line')
    
    # التنفيذ المطلق للإحداثيات في حالة الكاد
    if is_dxf:
        if shape_type == 'Straight Line' and 'abs_p1' in seg:
            p1, p2 = seg['abs_p1'], seg['abs_p2']
            px = p1[0] + ratio * (p2[0] - p1[0])
            py = p1[1] + ratio * (p2[1] - p1[1])
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            th = math.atan2(dy, dx)
            return px, py, th
            
        elif shape_type == 'Curve (Arc & Radius)' and 'abs_c' in seg:
            c, r = seg['abs_c'], seg['abs_r']
            current_ang = seg['abs_sa'] + ratio * seg.get('sweep', 0)
            px = c[0] + r * math.cos(current_ang)
            py = c[1] + r * math.sin(current_ang)
            th = current_ang + math.pi/2
            return px, py, th
            
    # التنفيذ البارامتري في حالة الإدخال اليدوي
    if start_data:
        x0, y0, th0, kappa = start_data.get('x0', 0), start_data.get('y0', 0), start_data.get('th0', 0), start_data.get('kappa', 0)
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
    if s_idx < 0 or s_idx >= len(segs): return 0.0, 0.0
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
    
    # سماحية دقيقة جداً لضمان الالتحام الكلي للنقاط
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
        
        # استخراج النقاط الهامة لكسر الفريم عندها إجبارياً
        key_s_vals = [0.0, L]
        for st_item in struts:
            if st_item.get('seg_idx') == i: key_s_vals.append(st_item['s_dist'])
        for ld in loads:
            if ld.get('seg_idx') == i:
                key_s_vals.append(ld['start'])
                key_s_vals.append(ld['end'])
        for sp in supports:
            if sp.get('seg_idx') == i: key_s_vals.append(sp['s_dist'])
            
        keys = list(key_s_vals)
        num_sub = max(1, int(np.ceil(L / mesh_size)))
        for p in np.linspace(0, L, num_sub+1): keys.append(p)
            
        # دقة 5 أرقام عشرية لمنع التشوهات
        keys = sorted(list(set([min(max(round(k, 5), 0.0), round(L, 5)) for k in keys])))
        
        node_indices = []
        for s_val in keys:
            px, py, _ = eval_seg_point(seg, s_val, seg_start_data[i])
            nid = get_or_add_node(px, py)
            node_indices.append(nid)
            if any(abs(s_val - kv) < 1e-4 for kv in key_s_vals):
                key_nodes.add(nid)
            
        sec_props = seg_sections[i]
        
        for j in range(len(keys)-1):
            n1, n2 = node_indices[j], node_indices[j+1]
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
                        
                        # تحليل قوى الحمل لمركبات تتوافق مع الساب 2000
                        if 'Global Z' in ld['dir']: # الحمل الرأسي
                            p_x1 += wa * s_t; p_y1 += wa * c_t
                            p_x2 += wb * s_t; p_y2 += wb * c_t
                        elif 'Global X' in ld['dir']: # الحمل الأفقي
                            p_x1 += wa * c_t; p_y1 -= wa * s_t
                            p_x2 += wb * c_t; p_y2 -= wb * s_t
                        else: # الحمل العمودي
                            p_x1 += 0.0; p_y1 += wa
                            p_x2 += 0.0; p_y2 += wb
                            
            elements.append({
                'type': 'frame', 'group': 'segment', 'sec': sec_props['name'],
                'n1': n1, 'n2': n2, 'px1': p_x1, 'py1': p_y1, 'px2': p_x2, 'py2': p_y2,
                'E': sec_props['E'] * 10000.0, 'A': sec_props['A'], 'I': sec_props['I'] / 100000000.0,
                'seg_idx': i, 's_start': keys[j], 's_end': keys[j+1], 'L': keys[j+1] - keys[j],
                'th_mid': th_mid
            })
            
        for ld in loads:
            if ld.get('seg_idx') == i and ld.get('type') == 'Point Load':
                try:
                    idx = keys.index(round(ld['start'], 5))
                    nid = node_indices[idx]
                    if 'Global Z' in ld['dir']:
                        nodal_loads.append({'node': nid, 'Fx': 0.0, 'Fy': ld['w1']})
                    elif 'Global X' in ld['dir']:
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
        gx = st_item.get('gx', 0.0)
        gy = st_item.get('gy', 0.0)
        
        nx, ny, _ = eval_seg_point(segments[seg_idx], dist, seg_start_data[seg_idx])
        
        top_node = get_or_add_node(nx, ny)
        bot_node = get_or_add_node(gx, gy)
        
        # النهايز عبارة عن عناصر ضغط وشد فقط Moment Released
        elements.append({
            'type': 'truss', 'group': 'strut', 'sec': st_item.get('sec', 'Unknown'),
            'n1': bot_node, 'n2': top_node, 'strut_idx': st_idx,
            'E': 21000000.0, 'A': 0.001
        })

    supports_list = []
    for sup in supports:
        if 'seg_idx' in sup:
            s_dist = round(sup['s_dist'], 5)
            nx, ny, _ = eval_seg_point(segments[sup['seg_idx']], s_dist, seg_start_data[sup['seg_idx']])
            nid = get_or_add_node(nx, ny)
        else:
            nid = get_or_add_node(sup.get('x', 0.0), sup.get('y', 0.0))
        supports_list.append({'node': nid, 'type': sup.get('type', 'Hinged'), 'angle': 0.0})
        
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
    except: U[free_dof] = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    R_reactions = K_orig @ U - F 
    
    for el in elements:
        if el.get('L', 0) < 1e-5: continue
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
# 4. Plotting Engine (Live Preview & SAP2000 Colors)
# =========================================================
def draw_base_geometry(ax, nodes, elements, supports_list, seg_sections=None, segments=None, seg_starts=None):
    for el in elements:
        if el['type'] not in ['frame', 'truss']: continue
        n1, n2 = nodes[el['n1']], nodes[el['n2']]
        if el['type'] == 'truss':
            ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color='red', linestyle='-', linewidth=0.8, zorder=1)
        else:
            if el.get('group') == 'base' and el.get('sec') == "None (Direct to Ground)": continue
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
            p1 = (x, y)
            p2 = (x + w, y - h)
            p3 = (x - w, y - h)
            ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.0, zorder=5))
            ax.plot([x - w - 0.05, x + w + 0.05], [y - h, y - h], color='limegreen', lw=1.0, zorder=4)
        elif t == 'Roller':
            h, w, r = 0.15, 0.12, 0.04
            p1 = (x, y)
            p2 = (x + w, y - h)
            p3 = (x - w, y - h)
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
                    ax.text(mid_x + nx_s*0.1, mid_y + ny_s*0.1, label, 
                            color='dimgray', fontsize=6, rotation=rot, ha='center', va='center', fontname='Arial')
        
        for i, seg in enumerate(segments):
            s_data = seg_starts[i]
            mx, my, mth = eval_seg_point(seg, seg.get('L', 0)/2, s_data)
            rot_deg = math.degrees(mth)
            if rot_deg > 90: rot_deg -= 180
            elif rot_deg < -90: rot_deg += 180
            sec_name = seg_sections[i]['name']
            label = f"S{i+1}: {get_short_name(sec_name)}"
            ax.text(mx - math.sin(mth)*0.1, my + math.cos(mth)*0.1, label, 
                    color='dimgray', fontsize=6, ha='center', va='center', rotation=rot_deg, fontname='Arial')

def draw_loads_and_geometry(ax, nodes, elements, supports_list, seg_sections, loads, segments, seg_starts):
    draw_base_geometry(ax, nodes, elements, supports_list, seg_sections, segments, seg_starts)

    scale_ld = 0.05
    for ld in loads:
        i = ld.get('seg_idx', 0)
        s_data = seg_starts[i]
        w1, w2 = ld.get('w1', 0), ld.get('w2', 0)
        
        num_pts = max(10, int((ld.get('end', 0) - ld.get('start', 0)) / 0.1))
        s_vals = np.linspace(ld.get('start', 0), ld.get('end', 0), num_pts)
        poly_pts = []
        top_pts = []
        
        for sv in s_vals:
            px, py, th = eval_seg_point(segments[i], sv, s_data)
            w_curr = w1 + (w2 - w1) * (sv - ld.get('start', 0)) / max(1e-5, (ld.get('end', 0) - ld.get('start', 0)))
            w_val = w_curr * scale_ld
            poly_pts.append((px, py))
            
            if 'Global Z' in ld.get('dir', ''):
                f_vx, f_vy = 0.0, w_val
            elif 'Global X' in ld.get('dir', ''):
                f_vx, f_vy = w_val, 0.0
            else:
                c, s = math.cos(th), math.sin(th)
                f_vx, f_vy = -s * w_val, c * w_val
                
            top_pts.append((px - f_vx, py - f_vy))
                
        poly_pts.extend(top_pts[::-1])
        if len(poly_pts) > 2:
            ax.add_patch(Polygon(poly_pts, facecolor='none', edgecolor='blue', lw=0.8, zorder=2))

            num_arrows = max(3, int((ld.get('end', 0) - ld.get('start', 0)) / 0.8))
            for k in range(num_arrows):
                frac = k / (num_arrows - 1) if num_arrows > 1 else 0.5
                sv = ld.get('start', 0) + frac * (ld.get('end', 0) - ld.get('start', 0))
                px_c, py_c, th_c = eval_seg_point(segments[i], sv, s_data)
                w_curr = w1 + frac * (w2 - w1)
                w_val = w_curr * scale_ld
                
                if 'Global Z' in ld.get('dir', ''):
                    f_vx, f_vy = 0.0, w_val
                elif 'Global X' in ld.get('dir', ''):
                    f_vx, f_vy = w_val, 0.0
                else:
                    c_c, s_c = math.cos(th_c), math.sin(th_c)
                    f_vx, f_vy = -s_c * w_val, c_c * w_val
                
                ax.arrow(px_c - f_vx, py_c - f_vy, f_vx, f_vy, head_width=0.05, head_length=0.1, length_includes_head=True, fc='blue', ec='blue', lw=0.5, zorder=3)

            px1, py1, th1 = eval_seg_point(segments[i], ld.get('start', 0), s_data)
            w_val_1 = w1 * scale_ld
            if 'Global Z' in ld.get('dir', ''): f_vx, f_vy = 0.0, w_val_1
            elif 'Global X' in ld.get('dir', ''): f_vx, f_vy = w_val_1, 0.0
            else:
                c_t, s_t = math.cos(th1), math.sin(th1)
                f_vx, f_vy = -s_t * w_val_1, c_t * w_val_1
            ax.text(px1 - f_vx, py1 - f_vy + 0.2, f"{w1:.1f}", color='black', fontsize=6, ha='center', fontname='Arial')

            px2, py2, th2 = eval_seg_point(segments[i], ld.get('end', 0), s_data)
            w_val_2 = w2 * scale_ld
            if 'Global Z' in ld.get('dir', ''): f_vx, f_vy = 0.0, w_val_2
            elif 'Global X' in ld.get('dir', ''): f_vx, f_vy = w_val_2, 0.0
            else:
                c_t, s_t = math.cos(th2), math.sin(th2)
                f_vx, f_vy = -s_t * w_val_2, c_t * w_val_2
            ax.text(px2 - f_vx, py2 - f_vy + 0.2, f"{w2:.1f}", color='black', fontsize=6, ha='center', fontname='Arial')

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
        Rx, Ry = R_reactions[3*n], R_reactions[3*n+1]
        x, y = nodes[n][0], nodes[n][1]
        
        if t == 'Roller':
            draw_reaction_arrow(ax_r, x, y, Ry, 0, 1)
        else:
            draw_reaction_arrow(ax_r, x, y, Rx, 1, 0)
            draw_reaction_arrow(ax_r, x, y, Ry, 0, 1)
            
    figs_dict['React'] = safe_render_fig(fig_r)
    
    def create_force_plot(val_key, scale, c_pos, c_neg):
        fig_f, ax_f = plt.subplots(figsize=(6, 5))
        ax_f.set_aspect('equal', adjustable='datalim')
        ax_f.axis('off')
        draw_base_geometry(ax_f, nodes, elements, supports_list, seg_sections, segments, seg_starts)
        
        global_texts = []
        def is_far(tx, ty):
            for (px, py) in global_texts:
                if math.hypot(tx-px, ty-py) < 0.35: return False
            return True

        global_max = 0.0
        for el in elements:
            if el['type'] == 'frame' and el.get('group') == 'segment': 
                global_max = max(global_max, np.max(np.abs(el.get('internal', {}).get(val_key, [0]))))

        for el in elements:
            if el['type'] != 'frame': continue
            n1, n2 = el['n1'], el['n2']
            x1, y1 = nodes[n1]
            c, s, L = el.get('c', 1), el.get('s', 0), el.get('L', 0)
            
            xs = el.get('internal', {}).get('x', [])
            vals = el.get('internal', {}).get(val_key, [])
            if len(vals) == 0: continue
            
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
            xs = el.get('internal', {}).get('x', [])
            v_rel = el.get('internal', {}).get('v_rel', np.zeros_like(xs)) * 20.0 
            if len(xs) == 0: continue
            
            x1, y1 = nodes[el['n1']]
            c, s = el.get('c', 1), el.get('s', 0)
            px = x1 + c * xs - s * v_rel
            py = y1 + s * xs + c * v_rel
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
    add_line(f"- Total System Length = {sum([s.get('L', 0) for s in sys_data['segments']]):.2f} m")
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
    
    st.info("💡 **Tip:** استخدم `Ctrl+Z` داخل أي مربع أرقام للاسترجاع وتصحيح الخطأ فوراً (مفعلة تلقائياً عبر متصفحك).")

    if 'adv_solved' not in st.session_state:
        st.session_state.adv_solved = False

    c_upload, c_mesh = st.columns([2, 1])
    with c_upload:
        uploaded_dxf = st.file_uploader("📥 Upload DXF File (.dxf)", type=['dxf'], key="dxf_uploader")
    with c_mesh:
        st.write("")
        st.write("")
        auto_mesh_size = st.number_input("Auto Frame Mesh Size (m)", min_value=0.05, max_value=5.0, value=0.25, step=0.05, help="أقصى طول لتقطيع القطاعات في الخلفية لزيادة دقة الدياجرامات")

    if uploaded_dxf and st.button("Extract Data from DXF"):
        for key in ['dxf_parsed', 'adv_fea_data', 'num_loads_override']:
            st.session_state.pop(key, None)
        st.session_state.adv_solved = False
        
        dxf_data = parse_dxf_to_data(uploaded_dxf.getvalue())
        if dxf_data:
            st.session_state.dxf_parsed = dxf_data
            st.session_state.num_loads_override = 0 
            st.success("✅ DXF Parsed Successfully! Geometry locked with absolute CAD coordinates. (J1 maintained at original 0,0 alignment)")
        else:
            st.error("❌ Failed to extract meaningful data. Check the format and try again.")

    dxf_data = st.session_state.get('dxf_parsed', None)
    num_loads_def = st.session_state.get('num_loads_override', 0 if dxf_data else 1)

    c_in, c_plot = st.columns([1.2, 1.8])
    
    with c_in:
        st.markdown("### 1. Supports & Base System")
        def_supp_count = len(dxf_data['supports']) if dxf_data else 2
        num_base_sups = st.number_input("Count of Point Supports", 0, 50, def_supp_count, on_change=reset_adv_state)
        base_sups = []
        for i in range(int(num_base_sups)):
            sp1, sp2 = st.columns(2)
            def_sx, def_sy = float(i*2.0), 0.0
            dxf_seg_idx, dxf_s_dist = None, None
            if dxf_data and i < len(dxf_data['supports']):
                def_sx = dxf_data['supports'][i].get('x', 0.0)
                def_sy = dxf_data['supports'][i].get('y', 0.0)
                dxf_seg_idx = dxf_data['supports'][i].get('seg_idx')
                dxf_s_dist = dxf_data['supports'][i].get('s_dist')
                
            sx = sp1.number_input(f"Sup J{i+1} X (m)", value=float(def_sx), format="%.5f", on_change=reset_adv_state, key=f"sx_{i}")
            styp = sp2.selectbox(f"Sup J{i+1} Type", ["Hinged", "Roller", "Fixed"], key=f"sp_{i}", on_change=reset_adv_state)
            
            sup_dict = {'x': sx, 'y': def_sy, 'type': styp}
            if dxf_seg_idx is not None and abs(sx - def_sx) < 1e-4:
                sup_dict['seg_idx'] = dxf_seg_idx
                sup_dict['s_dist'] = dxf_s_dist
            base_sups.append(sup_dict)

        st.markdown("### 2. Chain Segments Definition")
        num_segs = st.number_input("Number of Segments in Chain", min_value=1, max_value=50, value=len(dxf_data['segments']) if dxf_data else 1, on_change=reset_adv_state)
        seg_choices = [f"S{i+1}" for i in range(int(num_segs))]
        segments = []
        for i in range(int(num_segs)):
            with st.expander(f"⚙️ Segment S{i+1}", expanded=(num_segs<3)):
                s_type_idx = 0
                def_L, def_ang, def_R, def_S, def_smooth = 3.0, 60.0, 5.0, 3.0, True
                dir_crv_idx = 0
                
                is_dxf = False
                abs_p1, abs_p2, abs_c, abs_r, abs_sa, abs_ea, sweep = None, None, None, None, None, None, None
                
                if dxf_data and i < len(dxf_data['segments']):
                    d_seg = dxf_data['segments'][i]
                    is_dxf = d_seg.get('is_dxf', False)
                    s_type_raw = d_seg.get('Shape Type', d_seg.get('type', 'Straight Line'))
                    
                    if s_type_raw == 'Straight Line':
                        s_type_idx = 0
                        def_L = d_seg.get('L', 3.0)
                        def_ang = d_seg.get('start_angle', 60.0)
                        abs_p1 = d_seg.get('abs_p1')
                        abs_p2 = d_seg.get('abs_p2')
                        def_smooth = d_seg.get('smooth', False)
                    elif s_type_raw == 'Curve (Arc & Radius)':
                        s_type_idx = 1
                        def_R = d_seg.get('Radius (R) (m)', 5.0)
                        def_S = d_seg.get('L', 3.0)
                        def_ang = d_seg.get('start_angle', 0.0)
                        dir_crv_val = d_seg.get('Curvature Direction', "Arching Up ⤴ (Concave)")
                        dir_crv_idx = 0 if "Down" in dir_crv_val else 1
                        abs_c = d_seg.get('abs_c')
                        abs_r = d_seg.get('abs_r')
                        abs_sa = d_seg.get('abs_sa')
                        abs_ea = d_seg.get('abs_ea')
                        sweep = d_seg.get('sweep')
                        def_smooth = d_seg.get('smooth', False)

                if is_dxf:
                    st.success(f"🔒 DXF Geometry Locked: {s_type_raw} (L = {def_L:.3f}m)")
                    s_type = s_type_raw
                    L = def_L
                    kappa = d_seg.get('kappa', 0.0)
                    smooth = False
                    start_angle = def_ang
                else:
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
                        L = st.number_input(f"Length (L) (m) [S{i+1}]", value=float(def_L), step=0.5, format="%.5f", key=f"l_{i}", on_change=reset_adv_state)
                        kappa = 0.0
                    elif s_type == "Curve (Arc & Radius)":
                        r_val = st.number_input(f"Radius (R) (m) [S{i+1}]", value=float(def_R), step=0.5, format="%.5f", key=f"r_{i}", on_change=reset_adv_state)
                        L = st.number_input(f"Arc Length (S) (m) [S{i+1}]", value=float(def_S), step=0.5, format="%.5f", key=f"al_{i}", on_change=reset_adv_state)
                        dir_crv = st.selectbox(f"Curvature Direction [S{i+1}]", ["Arching Down ⤵ (Convex)", "Arching Up ⤴ (Concave)"], index=dir_crv_idx, key=f"d_{i}", on_change=reset_adv_state)
                        kappa = -1.0/r_val if "Down" in dir_crv else 1.0/r_val
                    else:
                        L_c = st.number_input(f"Chord Length (m) [S{i+1}]", value=4.0, step=0.5, format="%.5f", key=f"c_{i}", on_change=reset_adv_state)
                        h = st.number_input(f"Rise (m) [S{i+1}]", value=1.0, step=0.1, format="%.5f", key=f"h_{i}", on_change=reset_adv_state)
                        if h <= 0: h = 0.01
                        r_val = (L_c**2)/(8*h) + (h/2)
                        L = 2 * r_val * np.arcsin(L_c / (2*r_val))
                        dir_crv = st.selectbox(f"Curvature Direction [S{i+1}]", ["Arching Down ⤵ (Convex)", "Arching Up ⤴ (Concave)"], key=f"d2_{i}", on_change=reset_adv_state)
                        kappa = -1.0/r_val if "Down" in dir_crv else 1.0/r_val
                        st.info(f"💡 Calculated Arc Length = {L:.2f} m | Radius = {r_val:.2f} m")
                
                seg_info = {'type': s_type, 'Shape Type': s_type, 'L': L, 'kappa': kappa, 'smooth': smooth, 'start_angle': start_angle}
                if is_dxf:
                    seg_info['is_dxf'] = True
                    if s_type == "Straight Line":
                        seg_info['abs_p1'] = abs_p1
                        seg_info['abs_p2'] = abs_p2
                    elif s_type == "Curve (Arc & Radius)":
                        seg_info['abs_c'] = abs_c
                        seg_info['abs_r'] = abs_r
                        seg_info['abs_sa'] = abs_sa
                        seg_info['abs_ea'] = abs_ea
                        seg_info['sweep'] = sweep
                segments.append(seg_info)

        st.markdown("### 3. Properties & Sections")
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

        st.markdown("### 4. Applied Loads (Dead & Live)")
        num_loads = st.number_input("Count of Loads", 0, 30, num_loads_def, on_change=reset_adv_state)
        loads_data = []
        
        for i in range(int(num_loads)):
            with st.expander(f"📥 Load Item {i+1}", expanded=(i==0)):
                col_l1, col_l2, col_l3 = st.columns(3)
                load_category = col_l1.selectbox("Load Category", ["Dead Load", "Live Load"], key=f"ld_cat_{i}", on_change=reset_adv_state)
                l_type = col_l2.selectbox("Type", ["Uniform", "Trapezoidal", "Point Load"], key=f"ld_t_{i}", on_change=reset_adv_state)
                l_dir = col_l3.selectbox("Direction", ["Global Z (Vertical ↑+, ↓-)", "Global X (Horizontal →+, ←-)", "Local Z (Perpendicular ↗+, ↙-)"], key=f"ld_d_{i}", on_change=reset_adv_state)
                
                target_mode = st.radio("Apply Load To:", ["Single Segment", "Multiple Segments", "Total System (All Segments)"], key=f"ld_mode_{i}", horizontal=True, on_change=reset_adv_state)
                
                target_segments = []
                if target_mode == "Single Segment":
                    s_choice = st.selectbox("Select Segment", seg_choices, key=f"ld_single_{i}", on_change=reset_adv_state)
                    target_segments.append(int(s_choice[1:]) - 1)
                elif target_mode == "Multiple Segments":
                    selected_segs = st.multiselect("Select Segments", seg_choices, default=[seg_choices[0]] if seg_choices else [], key=f"ld_multi_{i}", on_change=reset_adv_state)
                    target_segments = [int(s[1:]) - 1 for s in selected_segs]
                else:
                    target_segments = list(range(int(num_segs)))
                
                sc1, sc2, sc3 = st.columns(3)
                w1 = sc1.number_input("Value W1 (kN/m or kN)", value=-15.0, key=f"ld_w1_{i}", on_change=reset_adv_state)
                w2_val = w1 if l_type != "Trapezoidal" else sc2.number_input("Value W2 (kN/m)", value=-5.0, key=f"ld_w2_{i}", on_change=reset_adv_state)
                
                for s_idx_num in target_segments:
                    max_s = float(segments[s_idx_num].get('L', 0.0))
                    start = sc1.number_input("Start Arc Dist (m)", 0.0, max_s, value=0.0, format="%.5f", key=f"ld_st_{i}_{s_idx_num}", on_change=reset_adv_state) if target_mode == "Single Segment" else 0.0
                    end = sc3.number_input("End Arc Dist (m)", 0.0, max_s, value=max_s, format="%.5f", key=f"ld_en_{i}_{s_idx_num}", on_change=reset_adv_state) if target_mode == "Single Segment" else max_s
                    if l_type == 'Point Load': end = start
                    
                    loads_data.append({
                        'seg_idx': s_idx_num, 
                        'category': load_category,
                        'type': l_type, 
                        'dir': l_dir, 
                        'start': start, 
                        'end': end, 
                        'w1': w1, 
                        'w2': w2_val
                    })

        st.markdown("### 5. Struts (Push-Pulls)")
        def_strut_count = len(dxf_data['struts']) if dxf_data else 1
        num_struts = st.number_input("Count of Struts", 0, 50, def_strut_count, on_change=reset_adv_state)
        struts_data = []
        
        raw_struts = list(STRUTS_DB.keys()) if STRUTS_DB else ["PPH 353"]
        def strut_priority(name):
            n = name.upper()
            if "PPS" in n: return 1
            if "PPH" in n: return 2
            if "TILT" in n: return 3
            if "MMP" in n: return 4
            return 5
        strut_opts = sorted(raw_struts, key=strut_priority)
        
        def get_approx_xy(segs, s_idx, s_val):
            if s_idx < 0 or s_idx >= len(segs): return 0.0, 0.0
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

        for i in range(int(num_struts)):
            with st.expander(f"📏 Strut P{i+1}", expanded=(num_struts<3)):
                def_s_idx = 0
                def_dist = float(segments[0].get('L', 0.0))/2 if segments else 1.0
                def_gx, def_gy = 3.0, 0.0
                
                is_dxf_strut = False
                if dxf_data and i < len(dxf_data['struts']):
                    ds = dxf_data['struts'][i]
                    def_s_idx = ds.get('seg_idx', 0)
                    def_dist = ds.get('dist', 0.0)
                    def_gx = ds.get('gx', 0.0)
                    def_gy = ds.get('gy', 0.0)
                    is_dxf_strut = True
                    
                if is_dxf_strut:
                    nx, ny = get_approx_xy(segments, def_s_idx, def_dist)
                    actual_L = math.hypot(def_gx - nx, def_gy - ny)
                    st.success(f"🔒 DXF Strut Mapped: S{def_s_idx+1} | Length: {actual_L:.2f}m")
                    
                    valid_opts = []
                    for opt in strut_opts:
                        m = re.search(r'\((\d+\.\d+):(\d+\.\d+)m\)', opt)
                        if m:
                            min_l, max_l = float(m.group(1)), float(m.group(2))
                            if min_l <= actual_L <= max_l:
                                valid_opts.append(opt)
                    if not valid_opts: 
                        valid_opts = strut_opts
                        st.warning("⚠️ لا يوجد ناهز يطابق هذا الطول في قاعدة البيانات!")
                    
                    st_sec = st.selectbox(f"P{i+1} Type", valid_opts, key=f"st_sec_{i}", on_change=reset_adv_state)
                    struts_data.append({'seg_idx': def_s_idx, 's_dist': def_dist, 'gx': def_gx, 'gy': def_gy, 'sec': st_sec})
                else:
                    cc1, cc2, cc3, cc4 = st.columns(4)
                    s_idx_num = min(def_s_idx, len(seg_choices) - 1)
                    s_idx = cc1.selectbox("On Seg No.", seg_choices, index=s_idx_num, key=f"st_s_{i}", on_change=reset_adv_state)
                    selected_idx = int(s_idx[1:]) - 1
                    max_s_strut = float(segments[selected_idx].get('L', 0.0))
                    safe_dist = min(max(float(def_dist), 0.0), max_s_strut)
                    dist = cc2.number_input("Arc Dist (m)", 0.0, max_s_strut, value=safe_dist, format="%.5f", key=f"st_d_{i}", on_change=reset_adv_state)
                    gx = cc3.number_input("Ground X (m)", value=float(def_gx), step=0.5, format="%.5f", key=f"st_gx_{i}", on_change=reset_adv_state)
                    
                    nx, ny = get_approx_xy(segments, selected_idx, dist)
                    actual_L = math.hypot(gx - nx, def_gy - ny)
                    
                    valid_opts = []
                    for opt in strut_opts:
                        m = re.search(r'\((\d+\.\d+):(\d+\.\d+)m\)', opt)
                        if m:
                            min_l, max_l = float(m.group(1)), float(m.group(2))
                            if min_l <= actual_L <= max_l:
                                valid_opts.append(opt)
                    if not valid_opts: 
                        valid_opts = strut_opts
                        st.warning("⚠️ لا يوجد ناهز يطابق هذا الطول في قاعدة البيانات!")

                    st_sec = cc4.selectbox(f"P{i+1} Type (L={actual_L:.2f}m)", valid_opts, key=f"st_sec_{i}", on_change=reset_adv_state)
                    struts_data.append({'seg_idx': selected_idx, 's_dist': dist, 'gx': gx, 'gy': def_gy, 'sec': st_sec})

    combined_loads = []
    dead_loads_only = [l for l in loads_data if l.get('category') == 'Dead Load']
    live_loads_only = [l for l in loads_data if l.get('category') == 'Live Load']
    combined_loads.extend(dead_loads_only)
    combined_loads.extend(live_loads_only)

    nodes, elements, nodal_loads, display_nodes, supports_list, seg_starts = build_chain_mesh(
        segments, seg_sections, combined_loads, struts_data, None, base_sups, {'type': 'Hinged', 'angle': 0.0}, mesh_size=auto_mesh_size)

    with c_plot:
        st.markdown("<h3 style='text-align: center; border-bottom: 2px solid #ddd; padding-bottom: 10px; font-family: Arial; color: #1e3d59;'>Live Geometry & Assignments</h3>", unsafe_allow_html=True)
        live_img = draw_live_preview(nodes, elements, supports_list, seg_sections, combined_loads, segments, seg_starts)
        st.image(live_img, use_container_width=True)

    st.markdown("---")
    
    col_btn, col_blank = st.columns([1.5, 2.5])
    with col_btn:
        if st.button("🚀 Run Advanced Chain Analysis", type="primary", use_container_width=True):
            with st.spinner("Generating Matrix & Solving (Combination: Dead + Live)..."):
                U, R = solve_fea_engine(nodes, elements, nodal_loads, supports_list)
                st.session_state.adv_fea_data = {
                    'U': U, 'R': R, 'nodes': nodes, 'elements': elements, 'display_nodes': display_nodes,
                    'supports_list': supports_list, 'seg_sections': seg_sections,
                    'loads_data': combined_loads, 'dead_loads': dead_loads_only, 'live_loads': live_loads_only,
                    'segments': segments, 'seg_starts': seg_starts
                }
                st.session_state.adv_solved = True
            st.success("✅ Analysis Complete based on (Dead + Live) Combination!")
            
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
        
        dead_img_buf = draw_live_preview(fea_data['nodes'], fea_data['elements'], fea_data['supports_list'], fea_data['seg_sections'], fea_data['dead_loads'], fea_data['segments'], fea_data['seg_starts'])
        live_img_buf = draw_live_preview(fea_data['nodes'], fea_data['elements'], fea_data['supports_list'], fea_data['seg_sections'], fea_data['live_loads'], fea_data['segments'], fea_data['seg_starts'])

        titles = {
            'Load': "Combined Load Diagram (Dead + Live)",
            'DeadLoad': "Dead Load Diagram",
            'LiveLoad': "Live Load Diagram",
            'React': "Reactions Diagram (kN)",
            'N': "Axial Force Diagram (kN)",
            'V': "Shear Force Diagram (kN)",
            'M': "Bending Moment Diagram (kN.m)",
            'D': "Deflection Deformed Shape"
        }
        
        c_p1, c_p2, c_p3 = st.columns(3)
        c_p1.image(dead_img_buf, use_container_width=True)
        c_p1.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['DeadLoad']}</p>", unsafe_allow_html=True)
        
        c_p2.image(live_img_buf, use_container_width=True)
        c_p2.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['LiveLoad']}</p>", unsafe_allow_html=True)
        
        c_p3.image(img_bufs['React'], use_container_width=True)
        c_p3.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['React']}</p>", unsafe_allow_html=True)
        
        c_p4, c_p5, c_p6 = st.columns(3)
        c_p4.image(img_bufs['N'], use_container_width=True)
        c_p4.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['N']}</p>", unsafe_allow_html=True)
        c_p5.image(img_bufs['V'], use_container_width=True)
        c_p5.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['V']}</p>", unsafe_allow_html=True)
        c_p6.image(img_bufs['M'], use_container_width=True)
        c_p6.markdown(f"<p style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px; font-family: Arial; font-size: 14px;'>{titles['M']}</p>", unsafe_allow_html=True)
        
        st.markdown("### 📊 Safety Summary (Combination: Dead + Live)")
        safety_data = []
        for i, sec in enumerate(fea_data['seg_sections']):
            max_m, max_v = 0.0, 0.0
            for el in fea_data['elements']:
                if el.get('group') == 'segment' and el.get('seg_idx') == i:
                    max_m = max(max_m, np.max(np.abs(el.get('internal', {}).get('M', [0]))))
                    max_v = max(max_v, np.max(np.abs(el.get('internal', {}).get('V', [0]))))
            
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

        fea_data['max_M'] = max([float(x['Actual'].split(',')[0].split('=')[1]) for x in safety_data]) if safety_data else 0
        fea_data['max_V'] = max([float(x['Actual'].split(',')[1].split('=')[1]) for x in safety_data]) if safety_data else 0
        fea_data['sec_props'] = {'name': "Mixed Sections", 'Mall': 999.0, 'Qall': 999.0}
        fea_data['img_bufs'] = img_bufs
        fea_data['safety_df'] = safety_data
        
        st.markdown("---")
        doc_out = generate_chain_report(fea_data)
        st.download_button("⬇️ Download Calculation Sheet (Word)", data=doc_out.getvalue(), file_name="Advanced_Shape_Calculation_Sheet.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

if __name__ == "__main__":
    render_advanced_shape_module()
