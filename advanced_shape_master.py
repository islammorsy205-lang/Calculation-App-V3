# advanced_shape_master.py

import streamlit as st
import numpy as np
import pandas as pd
import io
import os
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

try:
    from config import SECTIONS_DB, STRUTS_DB
    from report_builder import insert_blue_banner, add_eq, append_pdf_stream_to_word
except ImportError:
    st.error("⚠️ برجاء التأكد من وجود ملفات config.py و report_builder.py")

# =========================================================
# 1. Geometry & Mesh Generators (The Core Engine)
# =========================================================
def build_multi_segment_mesh(segments, sec_props, loads, struts, base_sec, supports, corner_sup):
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

    # 1. بناء الأضلاع المكسرة
    curr_x, curr_y = 0.0, 0.0
    seg_start_coords = []
    
    for i, seg in enumerate(segments):
        L = seg['L']
        ang_rad = np.radians(seg['angle'])
        seg_start_coords.append((curr_x, curr_y))
        
        keys = [0.0, L]
        for st_item in struts:
            if st_item['seg_idx'] == i: keys.append(st_item['dist'])
        for ld in loads:
            if ld['seg_idx'] == i:
                keys.append(ld['start'])
                keys.append(ld['end'])
        
        num_sub = max(1, int(np.ceil(L / 0.25)))
        for p in np.linspace(0, L, num_sub+1): keys.append(p)
            
        keys = sorted(list(set([round(k, 4) for k in keys if 0 <= k <= L + 1e-5])))
        
        node_indices = []
        for k in keys:
            px = curr_x + k * np.cos(ang_rad)
            py = curr_y + k * np.sin(ang_rad)
            node_indices.append(get_or_add_node(px, py))
            
        for j in range(len(keys)-1):
            n1, n2 = node_indices[j], node_indices[j+1]
            k_mid = (keys[j] + keys[j+1]) / 2.0
            
            p_x1, p_y1, p_x2, p_y2 = 0.0, 0.0, 0.0, 0.0
            
            for ld in loads:
                if ld['seg_idx'] == i and ld['type'] != 'Point Load':
                    if ld['start'] - 1e-4 <= k_mid <= ld['end'] + 1e-4:
                        L_ld = max(ld['end'] - ld['start'], 1e-5)
                        wa = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j] - ld['start']) / L_ld
                        wb = ld['w1'] + (ld['w2'] - ld['w1']) * (keys[j+1] - ld['start']) / L_ld
                        
                        if ld['dir'] == 'Gravity (Vertical ↓)':
                            p_y1 -= wa; p_y2 -= wb
                        else: 
                            c, s = np.cos(ang_rad), np.sin(ang_rad)
                            p_x1 += wa * s; p_y1 -= wa * c
                            p_x2 += wb * s; p_y2 -= wb * c
                            
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
                        c, s = np.cos(ang_rad), np.sin(ang_rad)
                        nodal_loads.append({'node': nid, 'Fx': ld['w1']*s, 'Fy': -ld['w1']*c})
                except ValueError: pass
                
        curr_x += L * np.cos(ang_rad)
        curr_y += L * np.sin(ang_rad)

    # 2. الأرضية أو الكمرة السفلية
    base_x_pts = [0.0]
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

    # 3. النهايز
    for st_item in struts:
        seg_idx = st_item['seg_idx']
        dist = st_item['dist']
        gx = st_item['gx']
        
        ang_rad = np.radians(segments[seg_idx]['angle'])
        sx, sy = seg_start_coords[seg_idx]
        nx = sx + dist * np.cos(ang_rad)
        ny = sy + dist * np.sin(ang_rad)
        
        top_node = get_or_add_node(nx, ny)
        bot_node = get_or_add_node(gx, 0.0)
        
        elements.append({
            'type': 'truss', 'group': 'strut', 'sec': st_item['sec'],
            'n1': bot_node, 'n2': top_node,
            'E': 21000000.0, 'A': 0.001
        })

    # 4. الركائز
    supports_list = []
    supports_list.append({'node': 0, 'type': corner_sup['type'], 'angle': corner_sup['angle']})
    for sup in supports:
        nid = get_or_add_node(sup['x'], 0.0)
        supports_list.append({'node': nid, 'type': sup['type'], 'angle': 0.0})
        
    display_nodes = set([s['node'] for s in supports_list])
    display_nodes.add(len(nodes)-1) 

    return nodes, elements, nodal_loads, display_nodes, supports_list, seg_start_coords

def build_curved_mesh(span, rise, num_segments, applied_w, point_load, sec_props, supports, struts_x_positions, strut_sec, base_sec):
    nodes = []
    elements = []
    nodal_loads = []
    
    if rise <= 0: rise = 0.01 
    R = (span**2) / (8 * rise) + (rise / 2)
    xc = span / 2
    yc = rise - R
    
    start_angle = np.arctan2(0 - yc, 0 - xc)
    end_angle = np.arctan2(0 - yc, span - xc)
    
    angles = np.linspace(start_angle, end_angle, num_segments + 1)
    for ang in angles:
        nx = xc + R * np.cos(ang)
        ny = yc + R * np.sin(ang)
        nodes.append([nx, ny])
        
    for i in range(num_segments):
        elements.append({
            'type': 'frame', 'group': 'segment', 'sec': sec_props['name'],
            'n1': i, 'n2': i+1,
            'px1': 0.0, 'py1': -applied_w, 'px2': 0.0, 'py2': -applied_w,
            'E': sec_props['E'] * 10000.0, 
            'A': sec_props['A'], 
            'I': sec_props['I'] / 100000000.0,
            'seg_idx': i
        })
        
    if abs(point_load) > 0.1:
        crown_idx = num_segments // 2
        nodal_loads.append({'node': crown_idx, 'Fx': 0.0, 'Fy': -point_load})

    supports_list = []
    supports_list.append({'node': 0, 'type': supports['start'], 'angle': 0.0})
    supports_list.append({'node': num_segments, 'type': supports['end'], 'angle': 0.0})
    
    base_x_pts = [0.0, span]
    for sx in struts_x_positions: base_x_pts.append(sx)
    base_x_pts = sorted(list(set([round(x, 4) for x in base_x_pts])))
    
    base_node_map = {}
    for x in base_x_pts:
        nodes.append([x, 0.0])
        base_node_map[x] = len(nodes) - 1

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

    for sx in struts_x_positions:
        distances = [abs(nodes[i][0] - sx) for i in range(num_segments + 1)]
        closest_node = np.argmin(distances)
        ground_node_idx = base_node_map[sx]
        
        supports_list.append({'node': ground_node_idx, 'type': 'Hinged', 'angle': 0.0})
        elements.append({
            'type': 'truss', 'group': 'strut', 'sec': strut_sec,
            'n1': ground_node_idx, 'n2': closest_node,
            'E': 21000000.0, 'A': 0.001
        })

    display_nodes = set([s['node'] for s in supports_list])
    display_nodes.add(num_segments)
    
    return nodes, elements, nodal_loads, display_nodes, supports_list

# =========================================================
# 2. Advanced FEA Solver
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
                [E*A/L, 0, 0, -E*A/L, 0, 0],
                [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L],
                [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2],
                [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/L**2, 4*E*I/L]
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
            
            xs = np.linspace(0, L, 11) 
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
# 3. Plotting Engine (Live Preview & SAP2000 Style)
# =========================================================
def safe_render_fig(fig):
    try:
        plt.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=300, bbox_inches='tight', pad_inches=0.01, transparent=True)
        return buf.getvalue()
    finally:
        plt.close(fig)

def draw_base_geometry(ax, nodes, elements, supports_list):
    for el in elements:
        if 'L' not in el: continue
        if el['group'] == 'base' and el['sec'] == "None (Direct to Ground)": continue
        n1, n2 = nodes[el['n1']], nodes[el['n2']]
        color = 'black' if el['type'] == 'frame' else 'gray'
        style = '-' if el['type'] == 'frame' else '--'
        ax.plot([n1[0], n2[0]], [n1[1], n2[1]], color=color, linestyle=style, linewidth=1.5, zorder=1)
        
    for sup in supports_list:
        n = sup['node']
        x, y = nodes[n][0], nodes[n][1]
        t = sup['type']
        if t == 'Fixed':
            ax.plot(x, y, marker='s', markerfacecolor='none', markeredgecolor='limegreen', markersize=6, zorder=5)
            ax.plot([x - 0.2, x + 0.2], [y - 0.2, y - 0.2], color='limegreen', lw=2, zorder=4)
        elif t == 'Hinged':
            ax.add_patch(Polygon([(x,y), (x+0.2,y-0.3), (x-0.2,y-0.3)], facecolor='none', edgecolor='limegreen', lw=1.2, zorder=5))
            ax.plot([x-0.3, x+0.3], [y-0.3, y-0.3], color='limegreen', lw=1.5, zorder=4)
        elif t == 'Roller':
            ax.add_patch(Polygon([(x,y), (x+0.2,y-0.3), (x-0.2,y-0.3)], facecolor='none', edgecolor='limegreen', lw=1.2, zorder=5))
            ax.add_patch(plt.Circle((x, y-0.38), 0.08, facecolor='none', edgecolor='limegreen', lw=1.2, zorder=5))
            ax.plot([x-0.3, x+0.3], [y-0.46, y-0.46], color='limegreen', lw=1.5, zorder=4)

def draw_live_preview(nodes, elements, supports_list, shape_mode, **kwargs):
    mpl.rcParams['font.family'] = 'sans-serif'
    mpl.rcParams['font.sans-serif'] = ['Arial']
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    
    draw_base_geometry(ax, nodes, elements, supports_list)

    if shape_mode == "🔗 Multi-Segment (Polygonal)":
        loads = kwargs.get('loads', [])
        segments = kwargs.get('segments', [])
        seg_starts = kwargs.get('seg_starts', [])
        
        for i, seg in enumerate(segments):
            sx, sy = seg_starts[i]
            ang = np.radians(seg['angle'])
            mx = sx + (seg['L']/2) * np.cos(ang)
            my = sy + (seg['L']/2) * np.sin(ang)
            ax.text(mx - np.sin(ang)*0.4, my + np.cos(ang)*0.4, f"Seg {i+1}", color='black', fontsize=7, ha='center', va='center', rotation=seg['angle'])

        scale_ld = 0.05
        for ld in loads:
            i = ld['seg_idx']
            ang = np.radians(segments[i]['angle'])
            c, s = np.cos(ang), np.sin(ang)
            sx, sy = seg_starts[i]
            
            px1, py1 = sx + ld['start']*c, sy + ld['start']*s
            px2, py2 = sx + ld['end']*c, sy + ld['end']*s
            w1, w2 = ld['w1'], ld['w2']
            
            if ld['type'] == 'Point Load':
                h = 1.0
                if ld['dir'] == 'Gravity (Vertical ↓)':
                    ax.arrow(px1, py1+h, 0, -h, head_width=0.1, head_length=0.2, length_includes_head=True, fc='blue', ec='blue')
                    ax.text(px1, py1+h+0.2, f"{w1} kN", color='blue', fontsize=7, ha='center')
                else:
                    start_x, start_y = px1 - s*h, py1 + c*h
                    ax.arrow(start_x, start_y, s*h, -c*h, head_width=0.1, head_length=0.2, length_includes_head=True, fc='blue', ec='blue')
                    ax.text(start_x, start_y+0.2, f"{w1} kN", color='blue', fontsize=7, ha='center', rotation=segments[i]['angle'])
            else:
                if ld['dir'] == 'Gravity (Vertical ↓)':
                    hx1, hy1 = px1, py1 + w1 * scale_ld
                    hx2, hy2 = px2, py2 + w2 * scale_ld
                else:
                    hx1, hy1 = px1 - s * w1 * scale_ld, py1 + c * w1 * scale_ld
                    hx2, hy2 = px2 - s * w2 * scale_ld, py2 + c * w2 * scale_ld
                    
                ax.add_patch(Polygon([(px1,py1), (hx1,hy1), (hx2,hy2), (px2,py2)], facecolor='royalblue', edgecolor='blue', alpha=0.3, zorder=2))
                ax.text(hx1, hy1+0.2, f"{w1}", color='blue', fontsize=6, ha='center')
                ax.text(hx2, hy2+0.2, f"{w2}", color='blue', fontsize=6, ha='center')
    else:
        applied_w = kwargs.get('applied_w', 0)
        if applied_w > 0.1:
            max_y = max([n[1] for n in nodes])
            scale_h = 1.0
            for el in elements:
                if el['type'] == 'frame' and el['group'] == 'segment':
                    n1, n2 = nodes[el['n1']], nodes[el['n2']]
                    x1, y1 = n1[0], n1[1]
                    x2, y2 = n2[0], n2[1]
                    h = scale_h
                    poly = Polygon([(x1,y1), (x1, y1+h), (x2, y2+h), (x2, y2)], facecolor='royalblue', edgecolor='blue', alpha=0.3, zorder=2)
                    ax.add_patch(poly)
                    dx, dy = x2-x1, y2-y1
                    num_arr = max(2, int(np.hypot(dx, dy) / 0.5))
                    for i in range(1, num_arr):
                        fx, fy = x1 + dx*(i/num_arr), y1 + dy*(i/num_arr)
                        ax.arrow(fx, fy+h, 0, -h*0.8, head_width=0.1, head_length=0.2, fc='blue', ec='blue', lw=0.5, zorder=3)
            
            mid_x = sum([n[0] for n in nodes])/len(nodes)
            ax.text(mid_x, max_y + scale_h + 0.3, f"{applied_w:.2f} kN/m", color='blue', fontsize=9, fontweight='bold', ha='center')

    # رسم النقط للمساعدة البصرية
    for i, n in enumerate(nodes):
        if any(el['n1'] == i or el['n2'] == i for el in elements if el['type'] == 'frame'):
            ax.plot(n[0], n[1], 'ko', markersize=2)

    return safe_render_fig(fig)

def plot_sap2000_diagrams(nodes, elements, R_reactions, scales, supports_list):
    mpl.rcParams['font.family'] = 'sans-serif'
    mpl.rcParams['font.sans-serif'] = ['Arial']
    figs_dict = {}
    
    # 1. Reactions
    fig_r, ax_r = plt.subplots(figsize=(8, 4))
    ax_r.set_aspect('equal', adjustable='datalim')
    ax_r.axis('off')
    draw_base_geometry(ax_r, nodes, elements, supports_list)
    
    for sup in supports_list:
        n = sup['node']
        Rx, Ry = R_reactions[3*n], R_reactions[3*n+1]
        x, y = nodes[n][0], nodes[n][1]
        if abs(Ry) > 0.1:
            arr_c = 'blue' if Ry > 0 else 'red'
            sgn = 1 if Ry > 0 else -1
            ax_r.plot([x, x], [y - sgn*0.8, y], color=arr_c, lw=1.0)
            ax_r.plot([x-0.15, x, x+0.15], [y-sgn*0.2, y, y-sgn*0.2], color=arr_c, lw=1.0)
            ax_r.text(x, y - sgn*1.0, f"{abs(Ry):.1f}", color='black', fontsize=8, ha='center', va='center')
            
        if abs(Rx) > 0.1:
            arr_c = 'blue' if Rx > 0 else 'red'
            sgn = 1 if Rx > 0 else -1
            ax_r.plot([x - sgn*0.8, x], [y, y], color=arr_c, lw=1.0)
            ax_r.plot([x-sgn*0.2, x, x-sgn*0.2], [y-0.15, y, y+0.15], color=arr_c, lw=1.0)
            ax_r.text(x - sgn*1.0, y, f"{abs(Rx):.1f}", color='black', fontsize=8, ha='center', va='center')
            
    figs_dict['React'] = safe_render_fig(fig_r)
    
    # 2. Internal Forces (Decluttered)
    def create_force_plot(val_key, scale, c_pos, c_neg):
        fig_f, ax_f = plt.subplots(figsize=(8, 4))
        ax_f.set_aspect('equal', adjustable='datalim')
        ax_f.axis('off')
        draw_base_geometry(ax_f, nodes, elements, supports_list)
        
        global_max = 0.0
        for el in elements:
            if el['type'] == 'frame' and el['group'] == 'segment': 
                global_max = max(global_max, np.max(np.abs(el['internal'][val_key])))

        for el in elements:
            if el['type'] != 'frame': continue
            n1, n2 = nodes[el['n1']], nodes[el['n2']]
            x1, y1 = n1[0], n1[1]
            c, s, L = el['c'], el['s'], el['L']
            
            xs = el['internal']['x']
            vals = el['internal'][val_key]
            plot_vals = -vals if val_key != 'N' else vals
            
            px = x1 + c * xs - s * plot_vals * scale
            py = y1 + s * xs + c * plot_vals * scale
            
            for k in range(len(px)-1):
                color = c_pos if vals[k] >= 0 else c_neg
                ax_f.plot([px[k], px[k+1]], [py[k], py[k+1]], color=color, lw=0.8)
                lx, ly = x1 + c*xs[k], y1 + s*xs[k]
                ax_f.plot([lx, px[k]], [ly, py[k]], color=color, lw=0.3, alpha=0.5)
                
            max_idx = np.argmax(np.abs(vals))
            v_max = abs(vals[max_idx])
            
            if v_max > 0.1 and (L > 0.4 or v_max >= global_max * 0.95):
                ax_f.text(px[max_idx]-s*0.3, py[max_idx]+c*0.3, f"{v_max:.1f}", fontsize=7, color='black', ha='center', va='center')
                
        return safe_render_fig(fig_f)

    figs_dict['N'] = create_force_plot('N', scales['N'], 'blue', 'red')
    figs_dict['V'] = create_force_plot('V', scales['V'], 'blue', 'red')
    figs_dict['M'] = create_force_plot('M', scales['M'], 'blue', 'red')
    
    # 3. Deflection
    fig_d, ax_d = plt.subplots(figsize=(8, 4))
    ax_d.set_aspect('equal', adjustable='datalim')
    ax_d.axis('off')
    draw_base_geometry(ax_d, nodes, elements, supports_list)
    
    max_def = 0.0
    for el in elements:
        if el['type'] == 'frame':
            xs = el['internal']['x']
            v_rel = el['internal']['v_rel'] * 20.0 # مقياس تكبير الرسم
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
# 4. Main Streamlit UI
# =========================================================
def render_advanced_shape_module():
    st.markdown("## 🎢 Advanced Shapes (Multi-Segment & Curved Plates)")
    
    c_in, c_plot = st.columns([1.2, 1])
    
    with c_in:
        shape_mode = st.radio("Select Shape Type:", ["🔗 Multi-Segment (Polygonal)", "🌙 Curved Arch (2 Plates)"], horizontal=True)
        
        st.markdown("### 1. Geometry & Section")
        sec_source = st.radio("Section Type:", ["Standard Profile Database", "Custom 2-Plates Steel", "Custom Input Properties"], horizontal=True)
        
        if sec_source == "Standard Profile Database":
            sec_list = list(SECTIONS_DB.keys()) if SECTIONS_DB else ["Soldier U100"]
            sec_name = st.selectbox("Profile Section", sec_list)
            raw = SECTIONS_DB.get(sec_name, {})
            s_E = raw.get('E', 2100.0)
            s_A = raw.get('A', raw.get('A_cm2', 34.3) / 10000.0)
            s_I = raw.get('I', raw.get('I_cm4', 412.0))
            sec_props = {'name': sec_name, 'E': s_E, 'A': s_A, 'I': s_I, 'Mall': raw.get('Mall', 13.1), 'Qall': raw.get('Qall', 100.8)}
            
        elif sec_source == "Custom 2-Plates Steel":
            cp1, cp2 = st.columns(2)
            p_height = cp1.number_input("Plates Depth / Height (mm)", value=300.0, step=10.0)
            p_thick = cp2.number_input("Plate Thickness (mm)", value=10.0, step=1.0)
            A_m2 = 2 * (p_height * p_thick) / 1000000.0
            I_cm4 = 2 * (p_thick * (p_height**3) / 12.0) / 10000.0
            m_all = st.number_input("Allowable Moment (kN.m)", value=30.0)
            v_all = st.number_input("Allowable Shear (kN)", value=150.0)
            sec_props = {'name': f"2 Plates ({int(p_height)}x{int(p_thick)}mm)", 'E': 2100.0, 'A': A_m2, 'I': I_cm4, 'Mall': m_all, 'Qall': v_all}
            st.success(f"⚙️ Calculated Area = {A_m2*1000000:.1f} mm² | Inertia (Ixx) = {I_cm4:.1f} cm⁴")
            
        else:
            cp1, cp2, cp3 = st.columns(3)
            A_val = cp1.number_input("Area (cm²)", value=34.3) / 10000.0
            I_val = cp2.number_input("Inertia Ixx (cm⁴)", value=412.0)
            Z_val = cp3.number_input("Section Modulus Z (cm³)", value=82.0)
            m_all = st.number_input("Allowable Moment (kN.m)", value=13.1)
            v_all = st.number_input("Allowable Shear (kN)", value=100.8)
            sec_props = {'name': "Custom Section", 'E': 2100.0, 'A': A_val, 'I': I_val, 'Mall': m_all, 'Qall': v_all}

        segments = []
        if shape_mode == "🔗 Multi-Segment (Polygonal)":
            num_segs = st.number_input("Number of Segments", min_value=1, max_value=10, value=3)
            st.markdown("**Segments Definition:**")
            for i in range(int(num_segs)):
                sc1, sc2 = st.columns(2)
                l_val = sc1.number_input(f"Length of Segment {i+1} (m)", value=2.0, step=0.5, key=f"l_{i}")
                a_val = sc2.number_input(f"Angle of Segment {i+1} (°)", value=0.0 if i==0 else 45.0, step=5.0, key=f"a_{i}")
                segments.append({'L': l_val, 'angle': a_val})
                
        else: # Curved Arch
            span = st.number_input("Arch Span / Chord (L) (m)", value=6.0, step=0.5)
            rise = st.number_input("Arch Rise (H) (m)", value=1.5, step=0.1)
            st.info(f"💡 Calculated Radius = **{(span**2)/(8*rise) + rise/2:.2f} m**")

        st.markdown("### 2. Loads & Supports")
        loads_data = []
        applied_w = 0.0
        point_load = 0.0
        
        if shape_mode == "🔗 Multi-Segment (Polygonal)":
            num_loads = st.number_input("Count of Loads", 1, 10, 1)
            for i in range(int(num_loads)):
                st.write(f"**Load {i+1}:**")
                lc1, lc2, lc3 = st.columns(3)
                s_idx = lc1.selectbox("On Segment No.", range(1, int(num_segs)+1), key=f"ld_s_{i}") - 1
                l_type = lc2.selectbox("Type", ["Uniform", "Trapezoidal", "Point Load"], key=f"ld_t_{i}")
                l_dir = lc3.selectbox("Direction", ["Gravity (Vertical ↓)", "Perpendicular (Local ↘)"], key=f"ld_d_{i}")
                
                sc1, sc2, sc3 = st.columns(3)
                start = sc1.number_input("Start Dist (m)", 0.0, float(segments[s_idx]['L']), 0.0, key=f"ld_st_{i}")
                w1 = sc2.number_input("Value W1 (kN/m or kN)", value=15.0, key=f"ld_w1_{i}")
                
                if l_type == "Uniform":
                    end = sc3.number_input("End Dist (m)", 0.0, float(segments[s_idx]['L']), float(segments[s_idx]['L']), key=f"ld_en_{i}")
                    w2 = w1
                elif l_type == "Trapezoidal":
                    end = sc3.number_input("End Dist (m)", 0.0, float(segments[s_idx]['L']), float(segments[s_idx]['L']), key=f"ld_en_{i}")
                    w2 = st.number_input("Value W2 (kN/m)", value=5.0, key=f"ld_w2_{i}")
                else:
                    end = start; w2 = w1
                    
                loads_data.append({'seg_idx': s_idx, 'type': l_type, 'dir': l_dir, 'start': start, 'end': end, 'w1': w1, 'w2': w2})
        else:
            applied_w = st.number_input("Uniform Vertical Gravity Load (kN/m)", value=25.0, step=1.0)
            point_load = st.number_input("Point Load at Crown (kN) [Optional]", value=0.0, step=1.0)
        
        st.markdown("**Supports & Struts:**")
        cs1, cs2 = st.columns(2)
        sup_start = cs1.selectbox("Start Node Support", ["Hinged", "Roller", "Fixed"])
        sup_end = cs2.selectbox("End Node Support", ["Roller", "Hinged", "Fixed"])
        supports = {'start': sup_start, 'end': sup_end}
        
        strut_opts = list(STRUTS_DB.keys()) if STRUTS_DB else ["PPH 353"]
        strut_sec = st.selectbox("Strut Type", strut_opts)
        
        struts_data = []
        struts_x_pos = []
        
        if shape_mode == "🔗 Multi-Segment (Polygonal)":
            num_struts = st.number_input("Count of Struts (Push-Pulls)", 0, 10, 1)
            for i in range(int(num_struts)):
                st.write(f"**Strut {i+1}:**")
                cc1, cc2, cc3 = st.columns(3)
                s_idx = cc1.selectbox("On Seg No.", range(1, int(num_segs)+1), key=f"st_s_{i}") - 1
                dist = cc2.number_input("Dist from Seg Start (m)", 0.0, float(segments[s_idx]['L']), float(segments[s_idx]['L'])/2, key=f"st_d_{i}")
                gx = cc3.number_input("Ground X (m)", value=float(i+1)*2.0, step=0.5, key=f"st_gx_{i}")
                struts_data.append({'seg_idx': s_idx, 'dist': dist, 'gx': gx, 'sec': strut_sec})
        else:
            st.write("Specify X-coordinates along the Span to place Struts:")
            num_struts = st.number_input("Number of Struts", min_value=0, max_value=10, value=2)
            scols = st.columns(4)
            for i in range(int(num_struts)):
                sx = scols[i%4].number_input(f"Strut {i+1} X (m)", value=(i+1)*span/(num_struts+1), step=0.5)
                struts_x_pos.append(sx)

        st.markdown("### 3. Base System (Optional)")
        bs1, bs2 = st.columns(2)
        base_sec_list = ["Soldier U100", "None (Direct to Ground)"]
        base_sec = bs1.selectbox("Base Soldier Profile", base_sec_list, index=1)
        
        c_sup = st.selectbox("Corner Support Type", ["Hinged", "Roller", "Fixed"])
        c_ang = st.number_input("Corner Angle (°)", value=0.0, step=15.0)
        
        num_base_sups = st.number_input("Additional Ground Supports", 0, 10, 1)
        base_sups = []
        for i in range(int(num_base_sups)):
            sp1, sp2 = st.columns(2)
            sx = sp1.number_input(f"Sup {i+1} X (m)", value=float(i+1)*2.0)
            styp = sp2.selectbox(f"Sup {i+1} Type", ["Hinged", "Roller", "Fixed"], key=f"sp_{i}")
            base_sups.append({'x': sx, 'type': styp})

    # Generate Mesh Live Preview
    if shape_mode == "🔗 Multi-Segment (Polygonal)":
        nodes, elements, nodal_loads, display_nodes, supports_list, seg_starts = build_multi_segment_mesh(segments, sec_props, loads_data, struts_data, base_sec, base_sups, {'type': c_sup, 'angle': c_ang})
        live_img = draw_live_preview(nodes, elements, supports_list, shape_mode, loads=loads_data, segments=segments, seg_starts=seg_starts)
    else:
        nodes, elements, nodal_loads, display_nodes, supports_list = build_curved_mesh(span, rise, 20, applied_w, point_load, sec_props, supports, struts_x_pos, strut_sec, base_sec)
        live_img = draw_live_preview(nodes, elements, supports_list, shape_mode, applied_w=applied_w)

    with c_plot:
        st.markdown("<h4 style='text-align: center; border-bottom: 1px solid gray; padding-bottom: 5px;'>Live Geometry & Loads</h4>", unsafe_allow_html=True)
        st.image(live_img, use_container_width=True)

    st.markdown("---")
    
    if st.button("🚀 Run Advanced Analysis", type="primary", use_container_width=True):
        with st.spinner("Generating Matrix & Solving..."):
            U, R = solve_fea_engine(nodes, elements, nodal_loads, supports_list)
            st.success("✅ Analysis Complete!")
            
            st.markdown("### 🎛️ Diagram Scales")
            c_s1, c_s2, c_s3 = st.columns(3)
            sc_n = c_s1.slider("Axial Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_v = c_s2.slider("Shear Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_m = c_s3.slider("Moment Scale", 0.001, 0.100, 0.015, step=0.001)
            
            img_bufs = plot_sap2000_diagrams(nodes, elements, R, {'N': sc_n, 'V': sc_v, 'M': sc_m}, supports_list)
            
            c_p1, c_p2 = st.columns(2)
            c_p1.image(img_bufs['M'], caption="Bending Moment Diagram (kN.m)")
            c_p2.image(img_bufs['V'], caption="Shear Force Diagram (kN)")
            c_p1.image(img_bufs['N'], caption="Axial Force Diagram (kN)")
            c_p2.image(img_bufs['React'], caption="Support Reactions (kN)")
            st.image(img_bufs['D'], caption="Deflection Deformed Shape")
            
            # --- Safety Checks Quick Table ---
            max_m, max_v = 0.0, 0.0
            for el in elements:
                if el['group'] == 'segment':
                    max_m = max(max_m, np.max(np.abs(el['internal']['M'])))
                    max_v = max(max_v, np.max(np.abs(el['internal']['V'])))
                    
            st.markdown("### 📊 Safety Summary")
            df = pd.DataFrame([
                {"Component": sec_props['name'], "Force Type": "Bending Moment", "Actual": f"{max_m:.2f} kN.m", "Allowable": f"{sec_props['Mall']:.2f} kN.m", "Status": "SAFE" if max_m <= sec_props['Mall'] else "UNSAFE"},
                {"Component": sec_props['name'], "Force Type": "Shear Force", "Actual": f"{max_v:.2f} kN", "Allowable": f"{sec_props['Qall']:.2f} kN", "Status": "SAFE" if max_v <= sec_props['Qall'] else "UNSAFE"}
            ])
            st.table(df)

if __name__ == "__main__":
    render_advanced_shape_module()
