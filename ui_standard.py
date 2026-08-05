# ui_standard.py

import streamlit as st
import pandas as pd
import fitz
import re
from helpers import get_val, get_idx, convert_transparent_to_pdf_stream
from config import SECTIONS_DB, STD_LENGTHS, SHORING_OPTIONS_SLAB, ECO_FORM_ALLOW, TECH_FORM_ALLOW, CIRCULAR_ALLOW
from math_solver import parse_loads_from_df, generate_hydrostatic_loads, get_scaffold_allowable, get_prop_allowable, solve_beam_advanced
from plot_core import draw_system_sketch, generate_acrow_diagrams

# سيتم استدعاء الرياح والسترونج باك كملفات مستقلة تماماً داخل هذا الملف
from ui_strongback import render_strongback_ui
from ui_wind import render_wind_tilting_ui

def render_slab_element(i, gamma_c, live_load, fw_load, def_sec, def_main):
    etype_opts = ["Slab", "Drop Panel", "Beam"]
    element_type = st.radio("Element Type:", etype_opts, key=f"etype_{i}", horizontal=True, index=get_idx("etype", i, etype_opts, 0))
    
    st.markdown("### 🧱 Load Config")
    if element_type == "Beam":
        cb1, cb2 = st.columns(2)
        with cb1: 
            b_width = st.number_input("Beam Width (m)", value=float(get_val("bw", i, 0.30)), step=0.05, key=f"bw_{i}")
        with cb2: 
            ts = st.number_input("Beam Depth (m)", value=float(get_val("ts_beam", i, 0.60)), step=0.05, key=f"ts_beam_{i}")
        bpos_opts = ["Edge", "Middle"]
        beam_pos = st.radio("Beam Position:", bpos_opts, key=f"bpos_{i}", horizontal=True, index=get_idx("bpos", i, bpos_opts, 0))
    else: 
        ts = st.number_input(f"{element_type} Thickness (m)", value=float(get_val("ts_thick", i, 0.30)), step=0.05, key=f"ts_thick_{i}")
        b_width = 0
        beam_pos = "None"
        
    w_tot = (gamma_c * ts) + live_load + fw_load
    st.info(f"**Total Basic Load** = {w_tot:.2f} kN/m²")
    p_th = "18.00 mm"
    p_mal = 54.0

    st.divider()
    with st.container(border=True):
        st.markdown("### 🪵 Secondary Beam")
        col_s1, col_s2 = st.columns([1, 1.2])
        with col_s1:
            s_sec = st.selectbox("Sec Section", list(SECTIONS_DB.keys()), index=get_idx("ss", i, list(SECTIONS_DB.keys()), def_sec), key=f"ss_{i}")
            s_spc = st.number_input("Spacing (Loaded Width) (m)", value=float(get_val("ssp", i, 0.350)), step=0.005, format="%.3f", key=f"ssp_{i}")
            s_w_calc = w_tot * s_spc
            
            s_l1_opts = STD_LENGTHS.get(s_sec, [3.0])
            c_sl1, c_sl2 = st.columns([3, 1])
            with c_sl2:
                st.markdown("<div style='margin-top: 32px;'></div>", unsafe_allow_html=True)
                cust_sL = st.checkbox("✏️ Custom", key=f"cust_sL_{i}")
            
            with c_sl1:
                if cust_sL:
                    s_L = st.number_input("Total Length (m)", min_value=0.1, value=float(s_l1_opts[-1]), step=0.1, key=f"num_sL_{i}")
                else:
                    s_l1_idx = get_idx("sl1", i, s_l1_opts, len(s_l1_opts)-1 if s_sec in STD_LENGTHS else 0)
                    s_L = st.selectbox("Total Length (m)", s_l1_opts, index=s_l1_idx, key=f"sl1_{i}")
            
            s_cl = st.number_input("L. Cant (m)", value=float(get_val("scl", i, 0.50)), step=0.05, key=f"scl_{i}")
            s_spns = st.text_input("Spans (m) [Comma sep]", value=str(get_val("sspn", i, "1.30, 1.30")), key=f"sspn_{i}")
            
            s_spans_list = [float(x.strip()) for x in s_spns.split(',') if x.strip()]
            s_cr = s_L - s_cl - sum(s_spans_list)
            
            if s_cr < -0.01: 
                st.error(f"❌ Error: Spans exceed total length!")
                
            auto_m_spc = 1.10
            if element_type == "Beam":
                auto_m_spc = b_width if beam_pos == "Edge" else b_width / 2.0
            else:
                if len(s_spans_list) > 0:
                    trib_widths = [s_cl + s_spans_list[0] / 2.0]
                    for j in range(1, len(s_spans_list)):
                        trib_widths.append((s_spans_list[j-1] + s_spans_list[j]) / 2.0)
                    right_cant = max(0.0, s_cr)
                    trib_widths.append(s_spans_list[-1] / 2.0 + right_cant)
                    auto_m_spc = max(trib_widths)
        
        with col_s2:
            st.markdown("**Load Assignment & Interactive Sketch**")
            if element_type == "Beam":
                def_s_la = max(0.0, (s_L / 2.0) - (b_width / 2.0) if beam_pos == "Middle" else s_cl - 0.05)
                def_s_lb = min(s_L, max(def_s_la, def_s_la + b_width))
            else: 
                def_s_la, def_s_lb = 0.0, s_L
                
            s_df = pd.DataFrame([{
                "Load Type": "Linear", 
                "WA (kN/m) or P (kN)": round(s_w_calc, 2), 
                "WB (kN/m)": round(s_w_calc, 2), 
                "LA (m) or X (m)": round(def_s_la, 2), 
                "LB (m)": round(def_s_lb, 2)
            }])
            
            s_loads_df = st.data_editor(
                s_df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"sdf_{i}",
                column_config={"Load Type": st.column_config.SelectboxColumn("Load Type", options=["Linear", "Trapezoidal", "Point"], required=True)}
            )
            s_loads_parsed = parse_loads_from_df(s_loads_df)
            s_supports = [s_cl] + [s_cl + sum(s_spans_list[:j+1]) for j in range(len(s_spans_list))]
                
            if s_L > 0 and s_cr >= -0.01: 
                c_pad1, c_img, c_pad2 = st.columns([1, 4, 1])
                with c_img: 
                    s_sketch_bytes = draw_system_sketch(s_L, s_supports, s_loads_parsed, transparent_bg=True)
                    st.image(s_sketch_bytes, use_container_width=True)
                    
                if st.toggle("📊 Show Analysis Diagrams", key=f"tgl_diag_s_slab_{i}"):
                    with st.spinner("Calculating..."):
                        s_img_bytes, _, _, _, _, _, _ = generate_acrow_diagrams(
                            s_sec, s_L, s_supports, s_loads_parsed, SECTIONS_DB[s_sec]['E'], SECTIONS_DB[s_sec]['I'], 
                            SECTIONS_DB[s_sec]['Mall'], SECTIONS_DB[s_sec]['Qall'], Rall=None, transparent_bg=True
                        )
                        col_dwn, img_col, _ = st.columns([1, 3, 1])
                        with col_dwn: 
                            st.download_button("📥 PDF", convert_transparent_to_pdf_stream(s_img_bytes), f"{s_sec}_Diagram.pdf", "application/pdf", key=f"dwn_s_slab_{i}")
                        with img_col: 
                            st.image(s_img_bytes, use_container_width=True)

        # حساب رد فعل السكندري أوتوماتيكياً في الخلفية
        try:
            _, _, _, _, s_R = solve_beam_advanced(s_L, s_supports, s_loads_parsed, SECTIONS_DB[s_sec]['E'], SECTIONS_DB[s_sec]['I'])
            max_s_reaction = max(s_R) if len(s_R) > 0 else 0.0
        except:
            max_s_reaction = 0.0

    st.divider()
    with st.container(border=True):
        st.markdown("### 🏗️ Main Beam & Shoring")
        col_m1, col_m2 = st.columns([1, 1.2])
        with col_m1:
            m_sec = st.selectbox("Main Section", list(SECTIONS_DB.keys()), index=get_idx("ms", i, list(SECTIONS_DB.keys()), def_main), key=f"ms_{i}")
            
            m_spc_def = round(auto_m_spc, 3)
            if st.session_state.get(f"last_auto_msp_{i}") != m_spc_def:
                st.session_state[f"msp_{i}"] = m_spc_def
                st.session_state[f"last_auto_msp_{i}"] = m_spc_def
                
            m_spc = st.number_input("Spacing (Loaded Width) (m)", value=m_spc_def, step=0.005, format="%.3f", key=f"msp_{i}")
            
            m_l1_opts = STD_LENGTHS.get(m_sec, [3.0])
            c_ml1, c_ml2 = st.columns([3, 1])
            with c_ml2:
                st.markdown("<div style='margin-top: 32px;'></div>", unsafe_allow_html=True)
                cust_mL = st.checkbox("✏️ Custom", key=f"cust_mL_{i}")
            
            with c_ml1:
                if cust_mL:
                    m_L = st.number_input("Total Length (m)", min_value=0.1, value=float(m_l1_opts[-1]), step=0.1, key=f"num_mL_{i}")
                else:
                    m_l1_idx = get_idx("wml1", i, m_l1_opts, len(m_l1_opts)-1 if m_sec in STD_LENGTHS else 0)
                    m_L = st.selectbox("Total Length (m)", m_l1_opts, index=m_l1_idx, key=f"wml1_{i}")
            
            m_cl = st.number_input("L. Cant (m)", value=float(get_val("mcl", i, 0.50)), step=0.05, key=f"mcl_{i}")
            m_spns = st.text_input("Spans (m) [Comma sep]", value=str(get_val("mspn", i, "1.20")), key=f"mspn_{i}")
            
            m_spans_list = [float(x.strip()) for x in m_spns.split(',') if x.strip()]
            m_cr = m_L - m_cl - sum(m_spans_list)
            
            if m_cr < -0.01: 
                st.error(f"❌ Error: Spans exceed total length!")
                
            t_nm = st.selectbox("Shoring Type", SHORING_OPTIONS_SLAB, index=get_idx("tn", i, SHORING_OPTIONS_SLAB, 0), key=f"tn_{i}")
            
            t_sub = ""
            t_unb = 0.0

            if t_nm == "Shorebrace Frame":
                st.info("✅ **Auto-Assigned:** Allowable Load = **54.40 kN / Leg**")
                t_al = 54.40
                
            elif t_nm == "Acrow Frame":
                st.info("✅ **Auto-Assigned:** Allowable Load = **22.25 kN / Leg**")
                t_al = 22.25
                
            elif t_nm == "Cup-lock":
                c_sup1, c_sup2 = st.columns(2)
                subtype = c_sup1.selectbox("Steel Grade", ["S355 (st.52)", "S235"], key=f"cup_sub_{i}")
                unbraced = c_sup2.number_input("Unbraced Length (Lcr) (m)", min_value=0.5, max_value=3.0, value=1.5, step=0.5, key=f"cup_unb_{i}")
                t_al = get_scaffold_allowable("Cup-lock", subtype, unbraced)
                st.info(f"✅ **Auto-Calculated Load:** {t_al:.2f} kN / Leg")
                t_sub = subtype
                t_unb = unbraced
                
            elif t_nm == "Ring-lock":
                c_sup1, c_sup2 = st.columns(2)
                subtype = c_sup1.selectbox("Diameter", ["Ringlock 1.5\"", "Ringlock 2.0\""], key=f"ring_sub_{i}")
                unbraced = c_sup2.number_input("Unbraced Length (Lcr) (m)", min_value=1.0, max_value=3.0, value=1.5, step=0.5, key=f"ring_unb_{i}")
                t_al = get_scaffold_allowable("Ring-lock", subtype, unbraced)
                st.info(f"✅ **Auto-Calculated Load:** {t_al:.2f} kN / Leg")
                t_sub = subtype
                t_unb = unbraced
                
            elif t_nm == "Acrow Prop":
                c_prop1, c_prop2, c_prop3 = st.columns([1.2, 1.5, 1])
                req_ext = c_prop1.number_input("Prop Extension (m)", min_value=0.95, max_value=5.50, value=3.20, step=0.1, key=f"prop_ext_{i}")
                
                from config import PROP_DB
                valid_props = [k for k, v in PROP_DB.items() if v['min'] <= req_ext <= v['max']]
                if not valid_props:
                    st.error("❌ No Acrow Prop fits this extension!")
                    t_al = 0.0
                else:
                    sel_prop = c_prop2.selectbox("Select Valid Prop", valid_props, key=f"prop_sel_{i}")
                    inner_up = c_prop3.toggle("Inner Tube UP?", value=True, key=f"prop_dir_{i}")
                    
                    t_al = get_prop_allowable(sel_prop, req_ext, inner_up)
                    st.success(f"✅ **Capacity:** {t_al:.2f} kN")
                    t_sub = sel_prop
                    
            else:
                t_al = st.number_input("Allowable (kN)", value=float(get_val("ta_man", i, 20.0)), step=0.5, key=f"ta_man_{i}")

        with col_m2:
            st.markdown("**Load Calculation Method:**")
            m_load_method = st.radio("Main Beam Load Source:", 
                                    ["Surface Pressure (W_tot × Main Spacing)", "Secondary Reaction (Max Reaction / Sec Spacing)"], 
                                    horizontal=True, key=f"m_l_meth_{i}", label_visibility="collapsed")
            
            if "Surface Pressure" in m_load_method:
                m_w_calc = w_tot * m_spc
            else:
                m_w_calc = max_s_reaction / s_spc if s_spc > 0 else 0.0
                
            st.markdown("**Load Assignment & Interactive Sketch**")
            m_df = pd.DataFrame([{
                "Load Type": "Linear", "WA (kN/m) or P (kN)": round(m_w_calc, 2), 
                "WB (kN/m)": round(m_w_calc, 2), "LA (m) or X (m)": 0.0, "LB (m)": m_L
            }])
            
            m_loads_df = st.data_editor(
                m_df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"mdf_{i}",
                column_config={"Load Type": st.column_config.SelectboxColumn("Load Type", options=["Linear", "Trapezoidal", "Point"], required=True)}
            )
            m_loads_parsed = parse_loads_from_df(m_loads_df)
            m_supports = [m_cl] + [m_cl + sum(m_spans_list[:j+1]) for j in range(len(m_spans_list))]
                
            if m_L > 0 and m_cr >= -0.01: 
                c_pad1, c_img, c_pad2 = st.columns([1, 4, 1])
                with c_img: 
                    m_sketch_bytes = draw_system_sketch(m_L, m_supports, m_loads_parsed, transparent_bg=True)
                    st.image(m_sketch_bytes, use_container_width=True)
                    
                if st.toggle("📊 Show Analysis Diagrams", key=f"tgl_diag_m_slab_{i}"):
                    with st.spinner("Calculating..."):
                        m_img_bytes, _, _, _, _, _, _ = generate_acrow_diagrams(
                            m_sec, m_L, m_supports, m_loads_parsed, SECTIONS_DB[m_sec]['E'], SECTIONS_DB[m_sec]['I'], 
                            SECTIONS_DB[m_sec]['Mall'], SECTIONS_DB[m_sec]['Qall'], Rall=t_al, transparent_bg=True
                        )
                        col_dwn, img_col, _ = st.columns([1, 3, 1])
                        with col_dwn: 
                            st.download_button("📥 PDF", convert_transparent_to_pdf_stream(m_img_bytes), f"{m_sec}_Diagram.pdf", "application/pdf", key=f"dwn_m_slab_{i}")
                        with img_col: 
                            st.image(m_img_bytes, use_container_width=True)
    
    return {
        "cat": "horizontal", "sub_cat": element_type, "ts": ts, "beam_b": b_width, "w": w_tot, 
        "ply_thick": p_th, "ply_mall": p_mal, "s_sec": s_sec, "s_spc": s_spc, "s_L": s_L, 
        "s_cl": s_cl, "s_sp": s_spns, "s_cr": s_cr, "s_ld": s_loads_parsed, "s_sup": s_supports, 
        "s_ld_img": s_sketch_bytes, "m_sec": m_sec, "m_spc": m_spc, "m_L": m_L, "m_cl": m_cl, 
        "m_sp": m_spns, "m_cr": m_cr, "m_ld": m_loads_parsed, "m_sup": m_supports, 
        "m_ld_img": m_sketch_bytes, "t_name": t_nm, "t_allow": t_al, "t_sub": t_sub, "t_unb": t_unb,
        "m_load_method": m_load_method, "max_s_rxn": max_s_reaction
    }

def render_vertical_element(i, element_subtype, def_sec, def_main):
    if f"hp_{i}" not in st.session_state: 
        st.session_state[f"hp_{i}"] = float(get_val("hp", i, 3.50))
        
    wall_pdf_curr = None
    t_nm = "Tie rod 15mm"
    t_al = 90.0
    section_data = {}
    
    if element_subtype == "Wall":
        wall_type = st.radio("Wall Type:", ["Double Sided Wall", "Single Sided Wall (Strongback)"], horizontal=True, key=f"wt_{i}") 
    else:
        wall_type = "Double Sided Wall"
        
    is_single_sided = (wall_type == "Single Sided Wall (Strongback)")
    
    if is_single_sided:
        vsys_opts = ["Timber H20 & Soldier System", "Acrow Beam S12 & Soldier System", "Eco-form Panel System", "Tech-form Panel System"]
    elif element_subtype == "Wall":
        vsys_opts = ["H20 & Soldier System", "VMC Panel System", "Eco-form Panel System", "Tech-form Panel System", "Curved Steel Panel System"]
    else:
        vsys_opts = ["H20 & Soldier System", "VMC Panel System", "Eco-form Panel System", "Tech-form Panel System", "Circular Steel Panel System"]
        
    vert_system = st.selectbox("Formwork System", vsys_opts, index=get_idx("vsys", i, vsys_opts, 0), key=f"vsys_{i}")
    is_panel_sys = "Panel" in vert_system
    
    st.markdown("### 🧱 Concrete Pressure Configuration")
    
    col_w1, col_w2, col_w3, col_w4 = st.columns(4)
    with col_w1:
        st.link_button("🔗 Open Acrow Concrete Pressure Calculator", "https://acrow-sdt.github.io/pressure-calculator/")
        wall_pdf_curr = st.file_uploader("Upload Pressure PDF (Auto-extracts Value):", type=['pdf'], key=f"wall_pdf_{i}")
    
    if wall_pdf_curr:
        text = "".join([page.get_text() for page in fitz.open(stream=wall_pdf_curr.read(), filetype="pdf")])
        match_p = re.search(r'(?i)(pressure|pmax|p\s*=)[\s:=]*([\d.]+)', text)
        w_tot = float(match_p.group(2)) if match_p else 47.16
        h_static = w_tot / 25.0
        st.success(f"✅ Auto-extracted: Pmax = {w_tot} kN/m², H static = {h_static:.2f} m")
        wall_pdf_curr.seek(0)
        with col_w2: 
            wall_h = st.number_input("Pouring Height (m)", value=float(st.session_state.get(f"hp_{i}", 3.50)), step=0.05, key=f"wall_ph_{i}")
    else:
        with col_w2: 
            w_tot = st.number_input("Concrete Pressure Pmax (kN/m²)", value=float(get_val("wall_p", i, 47.16)), step=0.05, key=f"wall_p_{i}")
        with col_w3: 
            h_static = st.number_input("H static (m)", value=float(get_val("wall_hs", i, w_tot/25.0)), step=0.05, key=f"wall_hs_{i}")
        with col_w4: 
            wall_h = st.number_input("Pouring Height (m)", value=float(st.session_state.get(f"hp_{i}", 3.50)), step=0.05, key=f"wall_ph_{i}")
            
    st.session_state[f"hp_{i}"] = wall_h
    
    m_spc_val = 1.0
    p_al_sys = 999.0
    panel_width = 0.0
    
    if is_panel_sys:
        if "Eco-form" in vert_system: 
            panel_w_opts = [0.30, 0.45, 0.60, 0.75, 0.90, 1.05]
            panel_width = st.selectbox("Select Panel Width (m)", panel_w_opts, index=get_idx("panel_w", i, panel_w_opts, 2), key=f"panel_w_{i}")
            p_al_sys = ECO_FORM_ALLOW.get(element_subtype, {}).get(panel_width, 90.0)
        elif "Tech-form" in vert_system: 
            p_al_sys = 80.0 if element_subtype == "Wall" else 100.0
        elif "Circular" in vert_system: 
            p_al_sys = 150.0
        elif "VMC" in vert_system: 
            p_al_sys = 70.0
        else: 
            p_al_sys = 80.0
        st.info(f"**Allowable Pressure for this Panel:** {p_al_sys} kN/m²")
    
    if not is_panel_sys:
        p_th = "18.00 mm"
        p_mal = 54.0
        st.divider()
        with st.container(border=True):
            st.markdown("### 🪵 Secondary Beam")
            col_s1, col_s2 = st.columns([1, 1.2])
            with col_s1:
                s_sec_opts = list(SECTIONS_DB.keys())
                def_s = s_sec_opts.index("Timber H20") if "H20" in vert_system else s_sec_opts.index("Acrow Beam S12")
                s_sec = st.selectbox("Sec Section", s_sec_opts, index=get_idx("wss", i, s_sec_opts, def_s), key=f"wss_{i}")
                s_spc = st.number_input("Spacing (Loaded Width) (m)", value=float(get_val("wssp", i, 0.310)), step=0.005, format="%.3f", key=f"wssp_{i}")
                
                wsl1_opts = STD_LENGTHS.get(s_sec, [3.0])
                c_wsl1, c_wsl2 = st.columns([3, 1])
                with c_wsl2:
                    st.markdown("<div style='margin-top: 32px;'></div>", unsafe_allow_html=True)
                    cust_wsl = st.checkbox("✏️ Custom", key=f"cust_wsl_{i}")
                
                with c_wsl1:
                    if cust_wsl:
                        s_L = st.number_input("Total Length (m)", min_value=0.1, value=float(wsl1_opts[-1]), step=0.1, key=f"num_wsl1_{i}")
                    else:
                        wsl1_idx = get_idx("wsl1", i, wsl1_opts, len(wsl1_opts)-1 if s_sec in STD_LENGTHS else 0)
                        s_L = st.selectbox("Total Length (m)", wsl1_opts, index=wsl1_idx, key=f"wsl1_{i}")
                
                s_cl = st.number_input("L. Cant (m)", value=float(get_val("wscl", i, 0.50)), step=0.05, key=f"wscl_{i}")
                s_spns = st.text_input("Spans (m) [Comma sep]", value=str(get_val("wsspn", i, "1.30, 1.30")), key=f"wsspn_{i}")
                
                s_spans_list = [float(x.strip()) for x in s_spns.split(',') if x.strip()]
                s_cr = s_L - s_cl - sum(s_spans_list)
                
                if s_cr < -0.01: 
                    st.error(f"❌ Error: Spans exceed total length!")
                    
                auto_m_spc = 1.30
                if len(s_spans_list) > 0:
                    trib_widths = [s_cl + s_spans_list[0] / 2.0]
                    for j in range(1, len(s_spans_list)):
                        trib_widths.append((s_spans_list[j-1] + s_spans_list[j]) / 2.0)
                    right_cant = max(0.0, s_cr)
                    trib_widths.append(s_spans_list[-1] / 2.0 + right_cant)
                    auto_m_spc = max(trib_widths)
                
            with col_s2:
                st.markdown("**Load Assignment & Interactive Sketch**")
                
                col_tog_s, col_inp_s = st.columns([1.2, 1])
                with col_tog_s:
                    is_hydro_s = st.toggle("Apply Hydrostatic Load (Trapezoidal) ✅", value=bool(get_val("wshydro", i, False)), key=f"wshydro_{i}")
                
                s_top_empty = 0.0
                if is_hydro_s:
                    with col_inp_s:
                        s_top_empty = st.number_input("Top Empty Dist. (m)", min_value=0.0, max_value=float(s_L), value=0.0, step=0.05, key=f"s_top_empty_{i}", help="المسافة الفاضية من أعلى الخشبة")

                if is_hydro_s: 
                    s_w_max = w_tot * s_spc
                    eff_h = s_L - s_top_empty
                    h_const = max(0.0, eff_h - h_static)
                    s_loads_data = []
                    
                    if h_const > 0:
                        s_loads_data.append({"Load Type": "Linear", "WA (kN/m) or P (kN)": round(s_w_max, 2), "WB (kN/m)": round(s_w_max, 2), "LA (m) or X (m)": 0.0, "LB (m)": round(h_const, 2)})
                    
                    if h_static > 0 and eff_h > h_const:
                        s_loads_data.append({"Load Type": "Trapezoidal", "WA (kN/m) or P (kN)": round(s_w_max, 2), "WB (kN/m)": 0.0, "LA (m) or X (m)": round(h_const, 2), "LB (m)": round(eff_h, 2)})
                        
                    if eff_h < s_L:
                        s_loads_data.append({"Load Type": "Linear", "WA (kN/m) or P (kN)": 0.0, "WB (kN/m)": 0.0, "LA (m) or X (m)": round(eff_h, 2), "LB (m)": round(s_L, 2)})
                        
                    if not s_loads_data:
                        s_loads_data = [{"Load Type": "Linear", "WA (kN/m) or P (kN)": 0.0, "WB (kN/m)": 0.0, "LA (m) or X (m)": 0.0, "LB (m)": round(s_L, 2)}]
                else: 
                    s_w_calc = w_tot * s_spc
                    s_loads_data = [{"Load Type": "Linear", "WA (kN/m) or P (kN)": round(s_w_calc, 2), "WB (kN/m)": round(s_w_calc, 2), "LA (m) or X (m)": 0.0, "LB (m)": s_L}]
                    
                s_df = pd.DataFrame(s_loads_data)
                s_loads_df = st.data_editor(
                    s_df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"wsdf_{i}",
                    column_config={"Load Type": st.column_config.SelectboxColumn("Load Type", options=["Linear", "Trapezoidal", "Point"], required=True)}
                )
                s_loads_parsed = parse_loads_from_df(s_loads_df)
                s_supports = [s_cl] + [s_cl + sum(s_spans_list[:j+1]) for j in range(len(s_spans_list))]
                
                if s_L > 0 and s_cr >= -0.01: 
                    c_pad1, c_img, c_pad2 = st.columns([1, 4, 1])
                    with c_img: 
                        s_sketch_bytes = draw_system_sketch(s_L, s_supports, s_loads_parsed, transparent_bg=True)
                        st.image(s_sketch_bytes, use_container_width=True)
                        
                    if st.toggle("📊 Show Analysis Diagrams", key=f"tgl_diag_s_wall_{i}"):
                        with st.spinner("Calculating..."):
                            ws_img_bytes, _, _, _, _, _, _ = generate_acrow_diagrams(
                                s_sec, s_L, s_supports, s_loads_parsed, SECTIONS_DB[s_sec]['E'], SECTIONS_DB[s_sec]['I'], 
                                SECTIONS_DB[s_sec]['Mall'], SECTIONS_DB[s_sec]['Qall'], Rall=None, transparent_bg=True
                            )
                            col_dwn, img_col, _ = st.columns([1, 3, 1])
                            with col_dwn: st.download_button("📥 PDF", convert_transparent_to_pdf_stream(ws_img_bytes), f"{s_sec}_Diagram.pdf", "application/pdf", key=f"dwn_s_wall_{i}")
                            with img_col: st.image(ws_img_bytes, use_container_width=True)

        # حساب رد فعل السكندري أوتوماتيكياً في الخلفية لحوائط
        try:
            _, _, _, _, s_R = solve_beam_advanced(s_L, s_supports, s_loads_parsed, SECTIONS_DB[s_sec]['E'], SECTIONS_DB[s_sec]['I'])
            max_s_reaction = max(s_R) if len(s_R) > 0 else 0.0
        except:
            max_s_reaction = 0.0

        st.divider()
        with st.container(border=True):
            st.markdown("### 🏗️ Main Beam" + ("" if is_single_sided else " & Tie Rod"))
            col_m1, col_m2 = st.columns([1, 1.2])
            with col_m1:
                m_sec = st.selectbox("Main Section", list(SECTIONS_DB.keys()), index=get_idx("wms", i, list(SECTIONS_DB.keys()), def_main), key=f"wms_{i}")
                
                m_spc_def = round(auto_m_spc, 3)
                if st.session_state.get(f"last_auto_wmsp_{i}") != m_spc_def:
                    st.session_state[f"wmsp_{i}"] = m_spc_def
                    st.session_state[f"last_auto_wmsp_{i}"] = m_spc_def
                    
                m_spc = st.number_input("Spacing (Loaded Width) (m)", value=m_spc_def, step=0.005, format="%.3f", key=f"wmsp_{i}")
                m_spc_val = m_spc
                
                wml1_opts = STD_LENGTHS.get(m_sec, [3.0])
                c_wml1, c_wml2 = st.columns([3, 1])
                with c_wml2:
                    st.markdown("<div style='margin-top: 32px;'></div>", unsafe_allow_html=True)
                    cust_wml = st.checkbox("✏️ Custom", key=f"cust_wml_{i}")
                
                with c_wml1:
                    if cust_wml:
                        m_L = st.number_input("Total Length (m)", min_value=0.1, value=float(wml1_opts[-1]), step=0.1, key=f"num_wml1_{i}")
                    else:
                        wml1_idx = get_idx("wml1_w", i, wml1_opts, len(wml1_opts)-1 if m_sec in STD_LENGTHS else 0)
                        m_L = st.selectbox("Total Length (m)", wml1_opts, index=wml1_idx, key=f"wml1_w_{i}")
                
                m_cl = st.number_input("L. Cant (m)", value=float(get_val("wmcl_w", i, 0.50)), step=0.05, key=f"wmcl_w_{i}")
                m_spns = st.text_input("Spans (m) [Comma sep]", value=str(get_val("wmspn_w", i, "1.32, 1.32")), key=f"wmspn_w_{i}")
                
                m_spans_list = [float(x.strip()) for x in m_spns.split(',') if x.strip()]
                m_cr = m_L - m_cl - sum(m_spans_list)
                
                if m_cr < -0.01: 
                    st.error(f"❌ Error: Spans exceed total length!")
                    
                if not is_single_sided: 
                    t_nm = "Tie rod 15mm"
                    st.text_input("Support Type", value=t_nm, disabled=True, key=f"wtn_{i}")
                    t_al = st.number_input("Allowable (kN)", value=90.0, disabled=True, key=f"wta_{i}")
                else: 
                    t_nm = "Strongback Truss"
                    t_al = 999.0
                
            with col_m2:
                st.markdown("**Load Calculation Method:**")
                m_load_method = st.radio("Main Beam Load Source:", 
                                        ["Surface Pressure (W_tot × Main Spacing)", "Secondary Reaction (Max Reaction / Sec Spacing)"], 
                                        horizontal=True, key=f"m_l_meth_{i}", label_visibility="collapsed")
                
                if "Surface Pressure" in m_load_method:
                    m_w_max = w_tot * m_spc
                else:
                    m_w_max = max_s_reaction / s_spc if s_spc > 0 else 0.0
                
                st.markdown("**Load Assignment & Interactive Sketch**")
                
                col_tog_m, col_inp_m = st.columns([1.2, 1])
                with col_tog_m:
                    is_hydro_m = st.toggle("Apply Hydrostatic Load (Trapezoidal) ✅", value=bool(get_val("wmhydro", i, False)), key=f"wmhydro_{i}")
                
                m_top_empty = 0.0
                if is_hydro_m:
                    with col_inp_m:
                        m_top_empty = st.number_input("Top Empty Dist. (m)", min_value=0.0, max_value=float(m_L), value=0.0, step=0.05, key=f"m_top_empty_{i}", help="المسافة الفاضية من أعلى الخشبة")

                if is_hydro_m: 
                    eff_h = m_L - m_top_empty
                    h_const = max(0.0, eff_h - h_static)
                    m_loads_data = []
                    
                    if h_const > 0:
                        m_loads_data.append({"Load Type": "Linear", "WA (kN/m) or P (kN)": round(m_w_max, 2), "WB (kN/m)": round(m_w_max, 2), "LA (m) or X (m)": 0.0, "LB (m)": round(h_const, 2)})
                    
                    if h_static > 0 and eff_h > h_const:
                        m_loads_data.append({"Load Type": "Trapezoidal", "WA (kN/m) or P (kN)": round(m_w_max, 2), "WB (kN/m)": 0.0, "LA (m) or X (m)": round(h_const, 2), "LB (m)": round(eff_h, 2)})
                        
                    if eff_h < m_L:
                        m_loads_data.append({"Load Type": "Linear", "WA (kN/m) or P (kN)": 0.0, "WB (kN/m)": 0.0, "LA (m) or X (m)": round(eff_h, 2), "LB (m)": round(m_L, 2)})
                        
                    if not m_loads_data:
                        m_loads_data = [{"Load Type": "Linear", "WA (kN/m) or P (kN)": 0.0, "WB (kN/m)": 0.0, "LA (m) or X (m)": 0.0, "LB (m)": round(m_L, 2)}]
                else: 
                    m_loads_data = [{"Load Type": "Linear", "WA (kN/m) or P (kN)": round(m_w_max, 2), "WB (kN/m)": round(m_w_max, 2), "LA (m) or X (m)": 0.0, "LB (m)": m_L}]
                    
                m_df = pd.DataFrame(m_loads_data)
                m_loads_df = st.data_editor(
                    m_df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"wmdf_{i}",
                    column_config={"Load Type": st.column_config.SelectboxColumn("Load Type", options=["Linear", "Trapezoidal", "Point"], required=True)}
                )
                m_loads_parsed = parse_loads_from_df(m_loads_df)
                m_supports = [m_cl] + [m_cl + sum(m_spans_list[:j+1]) for j in range(len(m_spans_list))]
                
                if m_L > 0 and m_cr >= -0.01: 
                    c_pad1, c_img, c_pad2 = st.columns([1, 4, 1])
                    with c_img: 
                        m_sketch_bytes = draw_system_sketch(m_L, m_supports, m_loads_parsed, transparent_bg=True)
                        st.image(m_sketch_bytes, use_container_width=True)
                        
                    if st.toggle("📊 Show Analysis Diagrams", key=f"tgl_diag_m_wall_{i}"):
                        with st.spinner("Calculating..."):
                            wm_img_bytes, _, _, _, _, _, _ = generate_acrow_diagrams(
                                m_sec, m_L, m_supports, m_loads_parsed, SECTIONS_DB[m_sec]['E'], SECTIONS_DB[m_sec]['I'], 
                                SECTIONS_DB[m_sec]['Mall'], SECTIONS_DB[m_sec]['Qall'], Rall=t_al if not is_single_sided else None, transparent_bg=True
                            )
                            col_dwn, img_col, _ = st.columns([1, 3, 1])
                            with col_dwn: st.download_button("📥 PDF", convert_transparent_to_pdf_stream(wm_img_bytes), f"{m_sec}_Diagram.pdf", "application/pdf", key=f"dwn_m_wall_{i}")
                            with img_col: st.image(wm_img_bytes, use_container_width=True)

    # =========================================================
    # استدعاء ملفات الـ Modules المستقلة (السترونج باك والرياح)
    # =========================================================
    if is_single_sided: 
        section_data['strongback'] = render_strongback_ui(i, w_tot, h_static, m_spc_val)
        
    st.divider()
    if st.toggle("🌬️ Include Wind Load Analysis & Tilting Check", value=False, key=f"wind_tog_{i}"):
        section_data['tilting'] = render_wind_tilting_ui(i, st.session_state.get(f"hp_{i}", h_static+0.5), w_tot)
    
    if is_panel_sys and not is_single_sided:
        st.divider()
        col_t, col_b = st.columns(2)
        tie_h, tie_v, bolt_h, bolt_v = 0.0, 0.0, 0.0, 0.0
        if vert_system != "Circular Steel Panel System":
            with col_t:
                st.markdown("#### 🔗 Tie Rod 15mm Configuration")
                tie_h = st.number_input("Tie Rod Horiz. Spacing (m)", value=float(get_val("tie_h", i, 1.20)), step=0.05, key=f"tie_h_{i}")
                tie_v = st.number_input("Tie Rod Vert. Spacing (m)", value=float(get_val("tie_v", i, 1.20)), step=0.05, key=f"tie_v_{i}")
                st.info("**Tie Rod Allowable Load:** 90.00 kN")
        with col_b:
            st.markdown("#### 🔩 Acrow Bolts Configuration")
            bolt_h = st.number_input("Bolt Horiz. Spacing (m)", value=float(get_val("bolt_h", i, 0.30)), step=0.05, key=f"bolt_h_{i}")
            bolt_v = st.number_input("Bolt Vert. Spacing (m)", value=float(get_val("bolt_v", i, 1.20)), step=0.05, key=f"bolt_v_{i}")
            st.info("**Bolt Allowable Tension/Shear:** 50.00 kN")
        section_data.update({'tie_h': tie_h, 'tie_v': tie_v, 'bolt_h': bolt_h, 'bolt_v': bolt_v})

    if not is_panel_sys:
        section_data.update({
            "cat": "vertical", "sub_cat": element_subtype, "sys_name": vert_system, "is_panel_system": False, 
            "height": st.session_state.get(f"hp_{i}", h_static+0.5) if not is_single_sided else h_static, 
            "w": w_tot, "ply_thick": p_th, "ply_mall": p_mal, "s_sec": s_sec, "s_spc": s_spc, "s_L": s_L, 
            "s_cl": s_cl, "s_sp": s_spns, "s_cr": s_cr, "s_ld": s_loads_parsed, "s_sup": s_supports, 
            "s_ld_img": s_sketch_bytes, "m_sec": m_sec, "m_spc": m_spc, "m_L": m_L, "m_cl": m_cl, 
            "m_sp": m_spns, "m_cr": m_cr, "m_ld": m_loads_parsed, "m_sup": m_supports, 
            "m_ld_img": m_sketch_bytes, "t_name": t_nm, "t_allow": t_al, "wall_pdf_curr": wall_pdf_curr,
            "m_load_method": m_load_method, "max_s_rxn": max_s_reaction
        })
    else:
        section_data.update({
            "cat": "vertical", "sub_cat": element_subtype, "sys_name": vert_system, "is_panel_system": True, 
            "w": w_tot, "panel_w": panel_width, "panel_allowable": p_al_sys, "wall_pdf_curr": wall_pdf_curr, 
            "height": st.session_state.get(f"hp_{i}", h_static+0.5) if not is_single_sided else h_static
        })
        
    return section_data
