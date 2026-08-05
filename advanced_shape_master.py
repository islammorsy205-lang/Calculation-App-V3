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
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# استدعاء قواعد البيانات ودوال النوتة من ملفاتك الأصلية
try:
    from config import SECTIONS_DB, STRUTS_DB
    from report_builder import insert_blue_banner, add_eq, append_pdf_stream_to_word
except ImportError:
    st.error("⚠️ برجاء التأكد من وجود ملفات config.py و report_builder.py")

# =========================================================
# 1. Geometry & Mesh Generators (The Core Engine)
# =========================================================
def build_multi_segment_mesh(segments, applied_w, sec_props, supports, struts):
    nodes = [[0.0, 0.0]]
    elements = []
    nodal_loads = []
    
    # 1. توليد النقط (Nodes)
    curr_x, curr_y = 0.0, 0.0
    for i, seg in enumerate(segments):
        L = seg['L']
        ang_rad = np.radians(seg['angle'])
        curr_x += L * np.cos(ang_rad)
        curr_y += L * np.sin(ang_rad)
        nodes.append([curr_x, curr_y])
        
    # 2. توليد العناصر (Elements) والأحمال
    for i in range(len(nodes)-1):
        x1, y1 = nodes[i]
        x2, y2 = nodes[i+1]
        
        elements.append({
            'type': 'frame', 'sec': sec_props['name'],
            'n1': i, 'n2': i+1,
            'px1': 0.0, 'py1': -applied_w, 'px2': 0.0, 'py2': -applied_w, # حمل منتظم رأسي
            'E': sec_props['E'] * 10000.0, 
            'A': sec_props['A'], 
            'I': sec_props['I'] / 100000000.0
        })
        
    # 3. معالجة الدعامات والنهايز (Supports & Struts)
    supports_list = []
    supports_list.append({'node': 0, 'type': supports['start'], 'angle': 0.0})
    supports_list.append({'node': len(nodes)-1, 'type': supports['end'], 'angle': 0.0})
    
    # إضافة النهايز كـ Truss Elements متصلة بالأرض
    for strut in struts:
        node_idx = strut['node_idx']
        strut_type = strut['sec']
        st_props = STRUTS_DB.get(strut_type, {'allow': 50.0})
        
        # إنشاء نقطة جديدة على الأرض تحت الـ Node مباشرة
        ground_x = nodes[node_idx][0]
        ground_y = 0.0
        nodes.append([ground_x, ground_y])
        ground_node_idx = len(nodes) - 1
        
        # تثبيت النقطة على الأرض
        supports_list.append({'node': ground_node_idx, 'type': 'Hinged', 'angle': 0.0})
        
        # توصيل الناهز
        elements.append({
            'type': 'truss', 'sec': strut_type,
            'n1': ground_node_idx, 'n2': node_idx,
            'E': 21000000.0, 'A': 0.001
        })
        
    display_nodes = set([s['node'] for s in supports_list])
    return nodes, elements, nodal_loads, display_nodes, supports_list

def build_curved_mesh(span, rise, num_segments, applied_w, sec_props, supports, struts_x_positions, strut_sec):
    nodes = []
    elements = []
    nodal_loads = []
    
    # حساب نصف القطر ومركز الدائرة بناءً على الوتر والارتفاع
    if rise <= 0: rise = 0.01 # حماية من القسمة على صفر
    R = (span**2) / (8 * rise) + (rise / 2)
    xc = span / 2
    yc = rise - R
    
    start_angle = np.arctan2(0 - yc, 0 - xc)
    end_angle = np.arctan2(0 - yc, span - xc)
    
    # توليد نقط الكيرف (Discretization)
    angles = np.linspace(start_angle, end_angle, num_segments + 1)
    for ang in angles:
        nx = xc + R * np.cos(ang)
        ny = yc + R * np.sin(ang)
        nodes.append([nx, ny])
        
    # توليد العناصر للكيرف
    for i in range(num_segments):
        elements.append({
            'type': 'frame', 'sec': sec_props['name'],
            'n1': i, 'n2': i+1,
            'px1': 0.0, 'py1': -applied_w, 'px2': 0.0, 'py2': -applied_w,
            'E': sec_props['E'] * 10000.0, 
            'A': sec_props['A'], 
            'I': sec_props['I'] / 100000000.0
        })
        
    supports_list = []
    supports_list.append({'node': 0, 'type': supports['start'], 'angle': 0.0})
    supports_list.append({'node': num_segments, 'type': supports['end'], 'angle': 0.0})
    
    # زرع النهايز (Struts) في أقرب نقطة للـ X المطلوبة
    for sx in struts_x_positions:
        # البحث عن أقرب Node لإحداثي X المطلوب
        distances = [abs(nodes[i][0] - sx) for i in range(num_segments + 1)]
        closest_node = np.argmin(distances)
        
        # إنشاء نقطة الدعم على الأرض
        ground_x = nodes[closest_node][0]
        ground_y = 0.0
        if ground_y < nodes[closest_node][1] - 0.1: # التأكد إن الكيرف مرفوع عن الأرض
            nodes.append([ground_x, ground_y])
            ground_node_idx = len(nodes) - 1
            supports_list.append({'node': ground_node_idx, 'type': 'Hinged', 'angle': 0.0})
            
            elements.append({
                'type': 'truss', 'sec': strut_sec,
                'n1': ground_node_idx, 'n2': closest_node,
                'E': 21000000.0, 'A': 0.001
            })

    display_nodes = set([s['node'] for s in supports_list])
    return nodes, elements, nodal_loads, display_nodes, supports_list

# =========================================================
# 2. Advanced 2D Frame FEA Solver (Matrix Method)
# =========================================================
def solve_fea_engine(nodes, elements, nodal_loads, supports_list):
    num_nodes = len(nodes)
    NDOF = num_nodes * 3
    K = np.zeros((NDOF, NDOF))
    F = np.zeros(NDOF)
    
    for el in elements:
        n1, n2 = el['n1'], el['n2']
        x1, y1 = nodes[n1][0], nodes[n1][1]
        x2, y2 = nodes[n2][0], nodes[n2][1]
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
            k_loc[0, 0] = E * A / L; k_loc[3, 3] = E * A / L
            k_loc[0, 3] = -E * A / L; k_loc[3, 0] = -E * A / L
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
            f_eq_loc = np.array([
                (2*px1 + px2)*L/6.0,
                (7*py1 + 3*py2)*L/20.0,
                (3*py1 + 2*py2)*L**2/60.0,
                (px1 + 2*px2)*L/6.0,
                (3*py1 + 7*py2)*L/20.0,
                -(2*py1 + 3*py2)*L**2/60.0
            ])
            f_eq_glob = T.T @ f_eq_loc
            dof_idx = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
            for r in range(6): F[dof_idx[r]] += f_eq_glob[r]
            
        k_glob = T.T @ k_loc @ T
        dof_idx = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
        for r in range(6):
            for col in range(6):
                K[dof_idx[r], dof_idx[col]] += k_glob[r, col]
                
    for nl in nodal_loads:
        F[3*nl['node'] + 0] += nl['Fx']
        F[3*nl['node'] + 1] += nl['Fy']
            
    K_orig = K.copy()
    fixed_dofs = []
    K_penalty = 1e12
    
    for sup in supports_list:
        n = sup['node']
        t = sup['type']
        a = sup['angle']
        if t == 'Fixed': fixed_dofs.extend([3*n, 3*n+1, 3*n+2])
        elif t == 'Hinged': fixed_dofs.extend([3*n, 3*n+1])
        elif t == 'Roller':
            if abs(a % 180) < 1e-5: fixed_dofs.append(3*n+1)
            elif abs((a - 90) % 180) < 1e-5: fixed_dofs.append(3*n)
            else:
                rad = np.radians(a)
                nx, ny = -np.sin(rad), np.cos(rad) 
                K[3*n, 3*n] += K_penalty * nx**2; K[3*n+1, 3*n+1] += K_penalty * ny**2
                K[3*n, 3*n+1] += K_penalty * nx * ny; K[3*n+1, 3*n] += K_penalty * nx * ny

    free_dof = [i for i in range(NDOF) if i not in fixed_dofs]
    K_ff = K[np.ix_(free_dof, free_dof)]
    F_f = F[free_dof]
    
    U = np.zeros(NDOF)
    try: U_f = np.linalg.solve(K_ff, F_f)
    except np.linalg.LinAlgError: U_f = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    U[free_dof] = U_f
    R_reactions = K_orig @ U - F 
    
    for el in elements:
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
                [E*A/L, 0, 0, -E*A/L, 0, 0],
                [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L],
                [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2],
                [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/el['L']**2, 4*E*I/el['L']]
            ])
            px1, py1, px2, py2 = el.get('px1',0), el.get('py1',0), el.get('px2',0), el.get('py2',0)
            f_eq_loc = np.array([
                (2*px1 + px2)*L/6.0, (7*py1 + 3*py2)*L/20.0, (3*py1 + 2*py2)*L**2/60.0,
                (px1 + 2*px2)*L/6.0, (3*py1 + 7*py2)*L/20.0, -(2*py1 + 3*py2)*L**2/60.0
            ])
            f_end = k_loc @ u_loc - f_eq_loc
            
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
# 3. SAP2000 Style Plotting Engine
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
            ax_r.arrow(x, y - sgn*0.8, 0, sgn*0.6, head_width=0.15, head_length=0.2, fc=arr_c, ec=arr_c, zorder=6)
            ax_r.text(x, y - sgn*1.0, f"{abs(Ry):.1f}", color='black', fontsize=8, ha='center', va='center')
    figs_dict['React'] = safe_render_fig(fig_r)
    
    # 2. Internal Forces (N, V, M)
    def create_force_plot(val_key, scale, c_pos, c_neg):
        fig_f, ax_f = plt.subplots(figsize=(8, 4))
        ax_f.set_aspect('equal', adjustable='datalim')
        ax_f.axis('off')
        draw_base_geometry(ax_f, nodes, elements, supports_list)
        
        for el in elements:
            if el['type'] != 'frame': continue
            n1, n2 = nodes[el['n1']], nodes[el['n2']]
            x1, y1 = n1[0], n1[1]
            c, s, L = el['c'], el['s'], el['L']
            
            xs_arr = el['internal']['x']
            vals = el['internal'][val_key]
            plot_vals = -vals if val_key != 'N' else vals
            
            px_arr = x1 + c * xs_arr - s * plot_vals * scale
            py_arr = y1 + s * xs_arr + c * plot_vals * scale
            
            for k in range(len(px_arr)-1):
                color = c_pos if vals[k] >= 0 else c_neg
                ax_f.plot([px_arr[k], px_arr[k+1]], [py_arr[k], py_arr[k+1]], color=color, lw=0.8)
                lx, ly = x1 + c*xs_arr[k], y1 + s*xs_arr[k]
                ax_f.plot([lx, px_arr[k]], [ly, py_arr[k]], color=color, lw=0.3, alpha=0.5)
                
            max_idx = np.argmax(np.abs(vals))
            if abs(vals[max_idx]) > 0.1:
                ax_f.text(px_arr[max_idx]-s*0.3, py_arr[max_idx]+c*0.3, f"{abs(vals[max_idx]):.1f}", fontsize=7, color='black', ha='center', va='center')
                
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
            max_def = max(max_def, np.max(np.abs(el['internal']['v_rel'])))
            
    if max_def > 0:
        ax_d.text(nodes[0][0], nodes[0][1]+1.0, f"Max Relative Deflection = {max_def*1000:.2f} mm", color='red', fontsize=10, fontweight='bold')
    figs_dict['D'] = safe_render_fig(fig_d)

    return figs_dict

# =========================================================
# 4. Main Streamlit UI
# =========================================================
def render_advanced_shape_module():
    st.markdown("## 🎢 Advanced Shapes (Multi-Segment & Curved Plates)")
    
    shape_mode = st.radio("Select Shape Type:", ["🔗 Multi-Segment (Polygonal)", "🌙 Curved Arch (2 Plates)"], horizontal=True)
    
    st.markdown("### 1. Geometry & Section")
    c1, c2, c3 = st.columns(3)
    
    if shape_mode == "🔗 Multi-Segment (Polygonal)":
        num_segs = c1.number_input("Number of Segments", min_value=1, max_value=10, value=3)
        sec_list = list(SECTIONS_DB.keys()) if SECTIONS_DB else ["Soldier U100"]
        sec_name = c2.selectbox("Profile Section", sec_list)
        sec_props = SECTIONS_DB.get(sec_name, {'name': sec_name, 'E': 2100.0, 'A': 0.00343, 'I': 412.0})
        sec_props['name'] = sec_name
        
        st.markdown("**Segments Definition:**")
        segments = []
        for i in range(int(num_segs)):
            sc1, sc2 = st.columns(2)
            l_val = sc1.number_input(f"Length of Segment {i+1} (m)", value=2.0, step=0.5, key=f"l_{i}")
            a_val = sc2.number_input(f"Angle of Segment {i+1} (°)", value=0.0 if i==0 else 45.0, step=5.0, key=f"a_{i}")
            segments.append({'L': l_val, 'angle': a_val})
            
    else: # Curved Arch
        span = c1.number_input("Arch Span / Chord (L) (m)", value=6.0, step=0.5)
        rise = c2.number_input("Arch Rise (H) (m)", value=1.5, step=0.1)
        st.info(f"💡 Calculated Radius = **{(span**2)/(8*rise) + rise/2:.2f} m**")
        
        st.markdown("**Custom 2-Plates Section:**")
        cp1, cp2 = st.columns(2)
        p_height = cp1.number_input("Plates Depth / Height (mm)", value=300.0, step=10.0)
        p_thick = cp2.number_input("Plate Thickness (mm)", value=10.0, step=1.0)
        
        # 💡 حسابات القطاع (2 Plates)
        A_mm2 = 2 * (p_height * p_thick)
        I_mm4 = 2 * (p_thick * (p_height**3) / 12.0)
        
        sec_props = {
            'name': f"2 Plates ({int(p_height)}x{int(p_thick)}mm)",
            'E': 2100.0, # Steel E in t/cm2
            'A': A_mm2 / 1000000.0, # Convert to m2
            'I': I_mm4 * 100.0 # Convert to cm4 for consistency with DB format
        }
        st.success(f"⚙️ Calculated Area = {A_mm2:.1f} mm² | Inertia (Ixx) = {I_mm4:.1f} mm⁴")

    st.markdown("### 2. Loads & Supports")
    cl1, cl2 = st.columns(2)
    applied_w = cl1.number_input("Uniform Vertical Gravity Load (kN/m)", value=25.0, step=1.0)
    
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
        st.write("Select Nodes to support with Struts to ground:")
        num_nodes = int(num_segs) + 1
        cols = st.columns(min(num_nodes, 8))
        for i in range(1, num_nodes-1): # Skip start and end nodes
            if cols[i%len(cols)].checkbox(f"Node {i}", value=True):
                struts_data.append({'node_idx': i, 'sec': strut_sec})
    else:
        st.write("Specify X-coordinates along the Span to place Struts:")
        num_struts = st.number_input("Number of Struts", min_value=0, max_value=10, value=2)
        scols = st.columns(4)
        for i in range(int(num_struts)):
            sx = scols[i%4].number_input(f"Strut {i+1} X (m)", value=(i+1)*span/(num_struts+1), step=0.5)
            struts_x_pos.append(sx)

    st.markdown("---")
    
    if st.button("🚀 Run Advanced Analysis", type="primary", use_container_width=True):
        with st.spinner("Generating Matrix & Solving..."):
            
            if shape_mode == "🔗 Multi-Segment (Polygonal)":
                nodes, elements, nodal_loads, display_nodes, supports_list = build_multi_segment_mesh(segments, applied_w, sec_props, supports, struts_data)
            else:
                nodes, elements, nodal_loads, display_nodes, supports_list = build_curved_mesh(span, rise, 20, applied_w, sec_props, supports, struts_x_pos, strut_sec)
                
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

# استدعاء الدالة عند استيراد الملف
if __name__ == "__main__":
    render_advanced_shape_module()
