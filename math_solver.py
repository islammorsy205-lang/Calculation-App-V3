# math_solver.py

import numpy as np
import pandas as pd
from config import SECTIONS_DB, SHORING_OPTIONS_SLAB

def fmt_name(name):
    return name.replace("\n", " ").strip()

def get_kz(z, exp):
    if z < 4.5: z = 4.5
    zg, alpha = {"B": (365.76, 7.0), "C": (274.32, 9.5), "D": (213.36, 11.5)}.get(exp, (365.76, 7.0))
    return 2.01 * ((z / zg) ** (2 / alpha))

def parse_loads_from_df(df):
    parsed = []
    for _, row in df.iterrows():
        l_type = str(row.get("Load Type", "Linear")).strip()

        # دالة ذكية لمنع انهيار البرنامج عند وجود خلايا فارغة أو None
        def safe_float(val, default=0.0):
            if pd.isna(val) or val is None:
                return float(default)
            v_str = str(val).strip().lower()
            if v_str in ['none', 'nan', '', 'null']:
                return float(default)
            try:
                return float(val)
            except ValueError:
                return float(default)

        wa = safe_float(row.get("WA (kN/m) or P (kN)", 0))
        
        wb_raw = row.get("WB (kN/m)", None)
        if pd.isna(wb_raw) or wb_raw is None or str(wb_raw).strip().lower() in ['none', 'nan', '', 'null']:
            wb = wa
        else:
            wb = safe_float(wb_raw, wa)
            
        la = safe_float(row.get("LA (m) or X (m)", 0))
        
        lb_raw = row.get("LB (m)", None)
        if pd.isna(lb_raw) or lb_raw is None or str(lb_raw).strip().lower() in ['none', 'nan', '', 'null']:
            lb = la
        else:
            lb = safe_float(lb_raw, la)

        if l_type == "Point":
            parsed.append({'type': 'point', 'p': wa, 'x': la})
        else:
            parsed.append({'type': 'linear', 'w1': wa, 'w2': wb, 'x1': min(la, lb), 'x2': max(la, lb)})
    return parsed

def generate_hydrostatic_loads(w_tot, h_static, L_total, spacing):
    loads = []
    w_base = w_tot * spacing
    if L_total <= h_static:
        w_top = w_base * (1 - L_total / h_static)
        loads.append({"Load Type": "Trapezoidal", "WA (kN/m) or P (kN)": round(w_base, 2), "WB (kN/m)": round(w_top, 2), "LA (m) or X (m)": 0.0, "LB (m)": round(L_total, 2)})
    else:
        loads.append({"Load Type": "Trapezoidal", "WA (kN/m) or P (kN)": round(w_base, 2), "WB (kN/m)": 0.0, "LA (m) or X (m)": 0.0, "LB (m)": round(h_static, 2)})
        loads.append({"Load Type": "Linear", "WA (kN/m) or P (kN)": 0.0, "WB (kN/m)": 0.0, "LA (m) or X (m)": round(h_static, 2), "LB (m)": round(L_total, 2)})
    return loads

def get_prop_allowable(prop_name, ext, is_inner_up):
    from config import PROP_DB
    if prop_name not in PROP_DB: return 20.0
    data = PROP_DB[prop_name]
    mode = "inner" if is_inner_up else "outer"
    pts = data[mode]
    keys = sorted(pts.keys())
    if ext <= keys[0]: return pts[keys[0]]
    if ext >= keys[-1]: return pts[keys[-1]]
    for i in range(len(keys)-1):
        k1, k2 = keys[i], keys[i+1]
        if k1 <= ext <= k2:
            if k1 == k2: return pts[k1]
            return pts[k1] + (pts[k2] - pts[k1]) * (ext - k1) / (k2 - k1)
    return 20.0

def get_scaffold_allowable(sys_type, subtype, unbraced):
    from config import CUPLOCK_DB, RINGLOCK_DB
    db = CUPLOCK_DB if sys_type == "Cup-lock" else RINGLOCK_DB
    if subtype not in db: return 30.0
    pts = db[subtype]
    keys = sorted(pts.keys())
    if unbraced <= keys[0]: return pts[keys[0]]
    if unbraced >= keys[-1]: return pts[keys[-1]]
    for i in range(len(keys)-1):
        k1, k2 = keys[i], keys[i+1]
        if k1 <= unbraced <= k2:
            if k1 == k2: return pts[k1]
            return pts[k1] + (pts[k2] - pts[k1]) * (unbraced - k1) / (k2 - k1)
    return 30.0

def solve_beam_advanced(L_total, supports_x, loads, E_val, I_val):
    supports_x = sorted(list(set(supports_x)))
    n_sup = len(supports_x)
    
    if E_val > 5000.0:
        EI = E_val * I_val * 0.0001
    else:
        EI = E_val * I_val * 0.001
        
    if EI <= 0: EI = 1000.0 
    
    crit_pts = [0.0, L_total] + list(supports_x)
    for ld in loads:
        if ld['type'] == 'point': crit_pts.append(ld['x'])
        else: crit_pts.extend([ld['x1'], ld['x2']])
    crit_pts = np.unique(np.round(crit_pts, 4))
    
    x_dense = []
    for i in range(len(crit_pts)-1):
        L_seg = crit_pts[i+1] - crit_pts[i]
        if L_seg > 0:
            num_sub = max(5, int(L_seg / 0.02) + 1)
            segment = np.linspace(crit_pts[i], crit_pts[i+1], num_sub)
            if i == len(crit_pts)-2: x_dense.extend(segment)
            else: x_dense.extend(segment[:-1])
    
    x_fem = np.array(x_dense)
    num_nodes = len(x_fem)
    
    NDOF = num_nodes * 2
    K = np.zeros((NDOF, NDOF))
    F = np.zeros(NDOF)
    
    for i in range(num_nodes - 1):
        L = x_fem[i+1] - x_fem[i]
        if L <= 0: continue
        k_el = (EI) / (L**3) * np.array([
            [12, 6*L, -12, 6*L],
            [6*L, 4*L**2, -6*L, 2*L**2],
            [-12, -6*L, 12, -6*L],
            [6*L, 2*L**2, -6*L, 4*L**2]
        ])
        idx = [2*i, 2*i+1, 2*i+2, 2*i+3]
        for r in range(4):
            for c in range(4):
                K[idx[r], idx[c]] += k_el[r, c]
                
    for ld in loads:
        if ld['type'] == 'point':
            idx = np.argmin(np.abs(x_fem - ld['x']))
            F[2*idx] -= ld['p']
        else:
            x1, x2 = ld['x1'], ld['x2']
            w1, w2 = ld['w1'], ld['w2']
            for i in range(num_nodes - 1):
                xi, xj = x_fem[i], x_fem[i+1]
                L = xj - xi
                if L <= 0: continue
                ov_x1 = max(x1, xi)
                ov_x2 = min(x2, xj)
                if ov_x2 > ov_x1 + 1e-5:
                    w_i = w1 + (w2 - w1) * (ov_x1 - x1) / (x2 - x1) if x2 > x1 else w1
                    w_j = w1 + (w2 - w1) * (ov_x2 - x1) / (x2 - x1) if x2 > x1 else w2
                    L_ov = ov_x2 - ov_x1
                    
                    if abs(ov_x1 - xi) < 1e-5 and abs(ov_x2 - xj) < 1e-5:
                        F[2*i] -= L * (7*w_i + 3*w_j) / 20.0
                        F[2*i+1] -= (L**2) * (3*w_i + 2*w_j) / 60.0
                        F[2*i+2] -= L * (3*w_i + 7*w_j) / 20.0
                        F[2*i+3] += (L**2) * (2*w_i + 3*w_j) / 60.0
                    else:
                        w_avg = (w_i + w_j) / 2.0
                        F_tot = w_avg * L_ov
                        dist_to_i = (ov_x1 + L_ov/2.0) - xi
                        F[2*i] -= F_tot * (1 - dist_to_i/L)
                        F[2*i+2] -= F_tot * (dist_to_i/L)
                        
    sup_idx = [np.argmin(np.abs(x_fem - sx)) for sx in supports_x]
    free_dof = list(range(NDOF))
    for sn in sup_idx:
        if 2*sn in free_dof:
            free_dof.remove(2*sn)
        
    K_ff = K[np.ix_(free_dof, free_dof)]
    F_f = F[free_dof]
    
    U = np.zeros(NDOF)
    try:
        U_f = np.linalg.solve(K_ff, F_f)
    except np.linalg.LinAlgError:
        U_f = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    U[free_dof] = U_f
    
    R_full = K @ U - F
    R = [R_full[2*sn] for sn in sup_idx]
    D_fem = U[0::2] * 1000.0  
    
    x_eval_list = list(x_fem)
    for sx in supports_x:
        if sx > 1e-5: x_eval_list.append(sx - 1e-5)
        if sx < L_total - 1e-5: x_eval_list.append(sx + 1e-5)
        
    for ld in loads:
        if ld['type'] == 'point':
            px = ld['x']
            if px > 1e-5: x_eval_list.append(px - 1e-5)
            if px < L_total - 1e-5: x_eval_list.append(px + 1e-5)
            
    x_eval = np.unique(np.sort(x_eval_list))
    
    D = np.interp(x_eval, x_fem, D_fem)
    
    V = np.zeros_like(x_eval)
    M = np.zeros_like(x_eval)
    
    for i in range(len(x_eval)):
        x_pt = x_eval[i]
        for j, sx in enumerate(supports_x):
            if x_pt > sx - 1e-7:
                V[i] += R[j]
                M[i] += R[j] * (x_pt - sx)
        for ld in loads:
            if ld['type'] == 'point':
                if x_pt > ld['x'] - 1e-7:
                    V[i] -= ld['p']
                    M[i] -= ld['p'] * (x_pt - ld['x'])
            else:
                x1, x2 = ld['x1'], ld['x2']
                if x_pt > x1:
                    end_x = min(x_pt, x2)
                    w1, w2 = ld['w1'], ld['w2']
                    w_end = w1 + (w2 - w1) * (end_x - x1) / (x2 - x1) if x2 > x1 else w1
                    L_ld = end_x - x1
                    V[i] -= (w1 + w_end) / 2.0 * L_ld
                    M[i] -= (w1 * L_ld * (x_pt - x1 - L_ld/2.0)) + (0.5 * (w_end - w1) * L_ld * (x_pt - x1 - 2.0 * L_ld / 3.0))

    return x_eval, V, M, D, R

def get_element_safety_details(conf, is_sec=True):
    sec = conf['s_sec'] if is_sec else conf['m_sec']
    prop = SECTIONS_DB[sec]
    L = conf['s_L'] if is_sec else conf['m_L']
    sup = conf['s_sup'] if is_sec else conf['m_sup']
    ld = conf['s_ld'] if is_sec else conf['m_ld']
    
    _, V, M, D, _ = solve_beam_advanced(L, sup, ld, prop['E'], prop['I'])
    max_M = np.max(np.abs(M)) if len(M) > 0 else 0
    max_V = np.max(np.abs(V)) if len(V) > 0 else 0
    max_D = np.max(np.abs(D)) if len(D) > 0 else 0
    
    max_span = 0
    if len(sup) > 1: max_span = max(np.diff(sup))
    allw_D = (max_span * 1000) / 400.0 if max_span > 0 else 10.0
    if "Timber" in sec and "H20" not in sec: allw_D = (max_span * 1000) * 0.003
    
    return max_M, max_V, max_D, prop['Mall'], prop['Qall'], allw_D

def solve_fea(nodes, elements, custom_loads, dist_loads):
    NDOF = len(nodes) * 3
    K = np.zeros((NDOF, NDOF))
    F = np.zeros(NDOF)
    
    for dl in dist_loads:
        y1, y2, w1, w2 = dl['y1'], dl['y2'], dl['w1'], dl['w2']
        for i, el in enumerate(elements):
            if el['mem'] == 'V':
                n1, n2 = el['n1'], el['n2']
                ny1, ny2 = nodes[n1][1], nodes[n2][1]
                L = abs(ny2 - ny1)
                ymin, ymax = min(ny1, ny2), max(ny1, max(ny1, ny2))
                overlap_y1 = max(y1, ymin)
                overlap_y2 = min(y2, ymax)
                if overlap_y2 > overlap_y1 + 1e-3:
                    w_start = w1 + (w2-w1)*(overlap_y1-y1)/(y2-y1) if y2 > y1 else w1
                    w_end = w1 + (w2-w1)*(overlap_y2-y1)/(y2-y1) if y2 > y1 else w2
                    L_ov = overlap_y2 - overlap_y1
                    w_avg = (w_start + w_end) / 2.0
                    F_total = w_avg * L_ov
                    d_center = overlap_y1 + L_ov/2.0 - ymin
                    r1 = F_total * (1 - d_center/L)
                    r2 = F_total * (d_center/L)
                    if ny1 < ny2:
                        F[3*n1] += r1
                        F[3*n2] += r2
                    else:
                        F[3*n1] += r2
                        F[3*n2] += r1

    if custom_loads:
        for ld in custom_loads:
            closest_j, min_dist = -1, 9999
            for j, n in enumerate(nodes):
                if abs(n[0]) < 1e-3: 
                    dist = abs(n[1] - ld['y'])
                    if dist < min_dist:
                        min_dist = dist
                        closest_j = j
            if min_dist < 0.1:
                idx = 3*closest_j + (2 if ld.get('is_moment') else 0)
                F[idx] += ld['p']

    for el in elements:
        n1, n2 = el['n1'], el['n2']
        x1, y1 = nodes[n1][0], nodes[n1][1]
        x2, y2 = nodes[n2][0], nodes[n2][1]
        L = np.hypot(x2 - x1, y2 - y1)
        if L < 1e-5: continue
        c, s = (x2 - x1) / L, (y2 - y1) / L
        el['c'], el['s'], el['L'] = c, s, L
        
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]
        ])
        
        # 💡 استدعاء الخصائص الفيزيائية الحقيقية من كائنات العناصر بدلاً من القيم الوهمية الثابتة
        E = el.get('E', 210000000.0)  
        A = el.get('A', 0.005)  
        I = el.get('I', 0.00005)  
        
        if el['type'] == 'truss' or el['mem'] == 'Tie':
            k_loc = np.zeros((6, 6))
            k_loc[0, 0] = E * A / L
            k_loc[3, 3] = E * A / L
            k_loc[0, 3] = -E * A / L
            k_loc[3, 0] = -E * A / L
        else:
            k_loc = np.array([
                [E*A/L, 0, 0, -E*A/L, 0, 0],
                [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
                [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L],
                [-E*A/L, 0, 0, E*A/L, 0, 0],
                [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2],
                [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/el['L']**2, 4*E*I/el['L']]
            ])
            
        k_glob = T.T @ k_loc @ T
        idx = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
        for i in range(6):
            for j in range(6):
                K[idx[i], idx[j]] += k_glob[i, j]

    free_dof = []
    for i, n in enumerate(nodes):
        if not n[2]: free_dof.append(3*i)
        if not n[3]: free_dof.append(3*i+1)
        if not n[4]: free_dof.append(3*i+2)
        
    K_ff = K[np.ix_(free_dof, free_dof)]
    F_f = F[free_dof]
    
    U = np.zeros(NDOF)
    try:
        U_f = np.linalg.solve(K_ff, F_f)
    except np.linalg.LinAlgError:
        U_f = np.linalg.lstsq(K_ff, F_f, rcond=None)[0]
    
    U[free_dof] = U_f

    R = K @ U - F
    
    for el in elements:
        n1, n2 = el['n1'], el['n2']
        idx = [3*n1, 3*n1+1, 3*n1+2, 3*n2, 3*n2+1, 3*n2+2]
        u_glob = U[idx]
        T = np.array([
            [el['c'], el['s'], 0, 0, 0, 0], [-el['s'], el['c'], 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, el['c'], el['s'], 0], [0, 0, 0, -el['s'], el['c'], 0], [0, 0, 0, 0, 0, 1]
        ])
        u_loc = T @ u_glob
        
        # 💡 استدعاء نفس الخصائص الفيزيائية الحقيقية لاستخراج القوى الداخلية بدقة
        E = el.get('E', 210000000.0)  
        A = el.get('A', 0.005)  
        I = el.get('I', 0.00005)  
        
        if el['type'] == 'truss' or el['mem'] == 'Tie':
            el['N_ax'] = (E * A / el['L']) * (u_loc[3] - u_loc[0])
            el['V'] = np.zeros(10)
            el['M'] = np.zeros(10)
            el['N'] = np.full(10, el['N_ax'])
            el['xs'] = np.linspace(0, el['L'], 10)
        else:
            k_loc = np.array([
                [E*A/el['L'], 0, 0, -E*A/el['L'], 0, 0],
                [0, 12*E*I/el['L']**3, 6*E*I/el['L']**2, 0, -12*E*I/el['L']**3, 6*E*I/el['L']**2],
                [0, 6*E*I/el['L']**2, 4*E*I/el['L'], 0, -6*E*I/el['L']**2, 2*E*I/el['L']],
                [-E*A/el['L'], 0, 0, E*A/el['L'], 0, 0],
                [0, -12*E*I/el['L']**3, -6*E*I/el['L']**2, 0, 12*E*I/el['L']**3, -6*E*I/el['L']**2],
                [0, 6*E*I/el['L']**2, 2*E*I/el['L'], 0, -6*E*I/el['L']**2, 4*E*I/el['L']]
            ])
            f_loc = k_loc @ u_loc
            V1, M1 = f_loc[1], f_loc[2]
            V2, M2 = f_loc[4], f_loc[5]
            
            w_loc = 0
            if el['mem'] == 'V':
                ny1, ny2 = nodes[n1][1], nodes[n2][1]
                ymin, ymax = min(ny1, ny2), max(ny1, ny2)
                for dl in dist_loads:
                    overlap_y1 = max(dl['y1'], ymin)
                    overlap_y2 = min(dl['y2'], ymax)
                    if overlap_y2 > overlap_y1 + 1e-3:
                        w_loc = (dl['w1'] + dl['w2']) / 2.0
                        break
                        
            xs = np.linspace(0, el['L'], 10)
            el['xs'] = xs
            el['V'] = V1 - w_loc * xs
            el['M'] = -M1 + V1 * xs - 0.5 * w_loc * xs**2
            el['N'] = np.full(10, f_loc[0])
            el['N_ax'] = f_loc[0]
            
    return R, U
