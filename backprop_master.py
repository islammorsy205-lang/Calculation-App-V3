# backprop_master.py

import streamlit as st
import numpy as np
import io
import os
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

try:
    from math_solver import get_prop_allowable, get_scaffold_allowable
except ImportError:
    st.error("⚠️ لم يتم العثور على math_solver.py. برجاء التأكد من مسار الملفات.")
    def get_prop_allowable(*args): return 20.0
    def get_scaffold_allowable(*args): return 30.0

def get_shoring_capacity(t_nm, subtype, unb, req_ext):
    t_al = 20.0
    if t_nm == "Shorebrace Frame":
        t_al = 54.00
    elif t_nm == "Cup-lock":
        t_al = get_scaffold_allowable("Cup-lock", subtype, unb)
    elif t_nm == "Ring-lock":
        t_al = get_scaffold_allowable("Ring-lock", subtype, unb)
    elif t_nm == "Acrow Prop":
        t_al = get_prop_allowable(subtype, req_ext, True)
    return t_al

def plot_zone_system(conf):
    results = []
    W_attacking = conf['W_fresh']
    results.append({'level': 'Fresh Slab', 'attacking': W_attacking, 'capacity': 0, 'transferred': W_attacking})
    
    current_P = W_attacking
    for i, slab in enumerate(conf['existing_slabs']):
        if current_P <= 0: break
        avail_cap = (slab['sidl'] + slab['ll']) * (slab['strength'] / 100.0)
        absorbed = min(current_P, avail_cap)
        current_P = max(0, current_P - avail_cap)
        
        results.append({
            'level': f'Existing Slab {i+1}', 
            'attacking': results[-1]['transferred'], 
            'capacity': avail_cap, 
            'transferred': current_P,
            'sidl': slab['sidl'],
            'll': slab['ll'],
            'strength': slab['strength']
        })
        
    num_levels = len(results)
    fig, ax = plt.subplots(figsize=(8, num_levels * 1.5))
    
    y_pos = np.arange(num_levels, 0, -1) * 2
    
    for i, res in enumerate(results):
        y = y_pos[i]
        color = 'gray' if 'Existing' in res['level'] else 'blue'
        rect = plt.Rectangle((1, y-0.2), 6, 0.4, facecolor=color, alpha=0.5, edgecolor='black', linewidth=2)
        ax.add_patch(rect)
        ax.text(4, y, res['level'], ha='center', va='center', fontsize=12, fontweight='bold', color='white')
        
        # =========================================================================
        # 💡 التعديل: إرجاع النص مجمعاً فوق البلاطة مع رفرفة بسيطة جهة اليمين
        # =========================================================================
        if 'Existing' in res['level']:
            combined_text = f"SIDL:{res['sidl']:.2f} | L.L:{res['ll']:.2f} (kN/m²)\nStrength achieved: {res['strength']:.0f}%"
            # الإحداثيات: x=7.4 (لعمل الرفرفة يمين البلاطة اللي بتنتهي عند 7.0)، y+0.25 (للجلوس فوق البلاطة مباشرة)
            ax.text(7.4, y + 0.25, combined_text, ha='right', va='bottom', fontsize=6, fontweight='normal', color='dimgray')
        
        if i < num_levels - 1 and res['transferred'] > 0:
            next_y = y_pos[i+1]
            ax.annotate('', xy=(4, next_y+0.2), xytext=(4, y-0.2),
                        arrowprops=dict(facecolor='red', shrink=0.05, width=4, headwidth=10))
            ax.text(4.2, (y + next_y)/2, f"{res['transferred']:.2f} kN/m²", color='red', fontsize=11, fontweight='bold')
            
            ax.plot([2.5, 2.5], [y-0.2, next_y+0.2], color='black', linewidth=3)
            ax.plot([5.5, 5.5], [y-0.2, next_y+0.2], color='black', linewidth=3)
            
    ax.set_xlim(0, 8.5) # تقليص المساحة البيضاء المتبقية على اليمين لضبط التنسيق
    ax.set_ylim(0, max(y_pos) + 1)
    ax.axis('off')
    plt.title("Load Transfer Diagram", fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)
    img_buf.seek(0)
    return img_buf

def generate_backprop_report(configs, ref_code):
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
        
    def add_p(text, bold=False, underline=False, color=None, size=12, indent=0):
        p = doc.add_paragraph()
        force_ltr_left(p)  
        
        p.paragraph_format.line_spacing = 1.5
        if indent > 0:
            p.paragraph_format.left_indent = Cm(indent)
            
        r = p.add_run(text)
        r.font.name = 'Arial'
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.underline = underline
        r.font.rtl = False  
        if color:
            r.font.color.rgb = color
        return p

    p_title = doc.add_paragraph()
    force_ltr_left(p_title)
    run_title = p_title.add_run("CALCULATION SHEET FOR RE-PROPPING (BACK-PROPPING)")
    run_title.font.name = 'Arial'
    run_title.font.size = Pt(16)
    run_title.font.bold = True
    run_title.font.rtl = False
    
    p_code = doc.add_paragraph()
    force_ltr_left(p_code)
    r_code = p_code.add_run("="*50 + f"\nCode Reference: {ref_code}")
    r_code.font.name = 'Arial'
    r_code.font.bold = True
    r_code.font.rtl = False

    for z_idx, conf in enumerate(configs):
        if z_idx > 0: doc.add_page_break()
        
        add_p(f"Zone {z_idx+1} Calculation:", bold=True, size=14, color=RGBColor(0, 0, 128))
        add_p("Design Loads for Slabs Back-Propping", bold=True, underline=True, color=RGBColor(192, 0, 0), size=14)
        
        add_p("A. Dead load:", indent=1)
        add_p(f"-  O.W of Concrete (Concrete density = {conf['gamma_c']:.1f} KN/m³)", indent=2)
        add_p(f"-  O.W of Formwork = {conf['FW']:.2f} KN/m²", indent=2)
        
        add_p("B. Live load:", indent=1)
        add_p(f"-  Live load = {conf['LL']:.2f} KN/m²", indent=2)
        
        add_p("\nLoad Acting on Existing Lower Slabs while Casting of Fresh Concrete Slabs.", bold=True, underline=True)
        add_p(f"-  Slabs: {conf['ts_fresh'] * 1000:.0f} mm.", indent=1)
        
        W_slab_str = f"W Slab (KN/m²) = O.W of Slab + Live Load + O.W of Formwork"
        add_p(W_slab_str, bold=True, indent=1)
        calc_str = f"               = {conf['gamma_c']:.1f}X{conf['ts_fresh']:.2f} + {conf['LL']:.2f} + {conf['FW']:.2f} = {conf['W_fresh']:.2f} KN/m²"
        add_p(calc_str, bold=True, indent=1)

        current_transferred = conf['W_fresh']
        
        for i, slab in enumerate(conf['existing_slabs']):
            if current_transferred <= 0: break
            
            add_p(f"\n❖ Characteristic Surface Loads for Critical Zone (Existing Slab {i+1}):", bold=True, underline=True)
            add_p(f"-  Super-imposed Dead Load (SDL) = {slab['sidl']:.2f} KN/m²", indent=1)
            add_p(f"-  Live Load (L.L) = {slab['ll']:.2f} KN/m²", indent=1)
            
            unfactored = slab['sidl'] + slab['ll']
            add_p(f"\nTotal un-Factored Load (W) = {slab['sidl']:.2f} + {slab['ll']:.2f} = {unfactored:.2f} KN/m²")
            add_p(f"Assume the concrete reach {slab['strength']:.0f}% from its strength.")
            capacity = unfactored * (slab['strength'] / 100.0)
            add_p(f"Therefore: - Total resisting load = {unfactored:.2f} x {slab['strength']/100:.2f} = {capacity:.2f} KN/m²")
            
            add_p(f"\nRe-Shoring Check for the System loaded on Existing Slab {i+1}", bold=True)
            add_p(f"➢ Total Re-Shoring Loads from upper level = {current_transferred:.2f} KN/m²", indent=1)
            
            next_transferred = max(0, current_transferred - capacity)
            add_p("Therefore; -", bold=True)
            add_p(f"The Transferred Loads to the Lower Level = {current_transferred:.2f} - {capacity:.2f} = {next_transferred:.2f} KN/m²")
            
            if next_transferred > 0:
                grid_i = slab['shore']
                add_p(f"Max. Loaded Area \"Back Propped area at Level {i+1}\" = {grid_i['gx']:.2f}x{grid_i['gy']:.2f} = {grid_i['area']:.2f} m²", indent=1)
                load_leg_i = grid_i['area'] * next_transferred
                
                check_txt_i = f"Area Load on one leg of {grid_i['sys']} = {grid_i['area']:.2f} x {next_transferred:.2f} = {load_leg_i:.2f} KN < {grid_i['cap']:.2f} KN"
                
                p_check_i = doc.add_paragraph()
                force_ltr_left(p_check_i) 
                p_check_i.paragraph_format.line_spacing = 1.5
                p_check_i.paragraph_format.left_indent = Cm(1)
                
                r_ci = p_check_i.add_run(check_txt_i)
                r_ci.font.name = 'Arial'
                r_ci.font.size = Pt(12)
                r_ci.font.rtl = False
                
                r_resi = p_check_i.add_run("   SAFE" if load_leg_i <= grid_i['cap'] else "   UNSAFE")
                r_resi.font.name = 'Arial'
                r_resi.font.size = Pt(12)
                r_resi.font.bold = True
                r_resi.font.rtl = False
                r_resi.font.color.rgb = RGBColor(0, 128, 0) if load_leg_i <= grid_i['cap'] else RGBColor(255, 0, 0)
                
            current_transferred = next_transferred

        levels_propped = conf['levels_propped']
        add_p("\n=======================================================", bold=True)
        add_p(f"FINAL CONCLUSION FOR ZONE {z_idx+1}:", bold=True, color=RGBColor(0, 128, 0))
        add_p(f"Total Number of Floors Required to be Propped = {levels_propped} Floors", bold=True)
        
        if levels_propped == 1:
            add_p("(This means ONLY the main shoring under the fresh slab is needed. The first existing slab can safely carry the transferred load, and NO back-propping is required underneath it.)", color=RGBColor(100,100,100))
        else:
            add_p(f"(This includes 1 floor of main shoring directly under the fresh slab, plus {levels_propped - 1} floor(s) of back-propping underneath the existing slabs.)", color=RGBColor(100,100,100))
        add_p("=======================================================\n", bold=True)

        add_p("Load Path Diagram:", bold=True)
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pPr = p_img._element.get_or_add_pPr()
        bidi = OxmlElement('w:bidi')
        bidi.set(qn('w:val'), '0')
        pPr.append(bidi)
        
        p_img.add_run().add_picture(io.BytesIO(conf['img_buf'].read()), width=Cm(16.0))
            
    out = io.BytesIO()
    doc.save(out)
    return out

def render_backprop_module(ref_code):
    st.markdown("## 🏗️ Multi-Zone Slab Re-propping (Back-propping)")
    st.info("💡 **Independent Module:** Evaluate multiple independent zones of fresh slabs. Each floor can have a distinct shoring grid and system.")
    
    LL_const = 1.50 if "BS" in ref_code else 2.40
    FW_load = 0.50
    st.success(f"**Code Detected:** {ref_code}  →  Construction Live Load = {LL_const} kN/m², Formwork = {FW_load} kN/m²")
    
    num_zones = st.number_input("Number of Fresh Slab Zones to Check", min_value=1, max_value=5, value=1)
    tabs = st.tabs([f"Zone {i+1}" for i in range(int(num_zones))])
    
    configs = []
    sys_opts = ["Acrow Prop", "Cup-lock", "Ring-lock", "Shorebrace Frame"]
    
    for idx, tab in enumerate(tabs):
        with tab:
            st.markdown(f"### Zone {idx+1} Properties")
            c1, c2 = st.columns(2)
            gamma_c = c1.number_input("Concrete Density (kN/m³)", value=25.0, step=0.5, key=f'g_{idx}')
            ts_fresh = c2.number_input("Fresh Slab Thickness (m)", value=0.28, step=0.01, key=f'ts_{idx}')
            W_fresh = (gamma_c * ts_fresh) + LL_const + FW_load
            st.info(f"**Total Fresh Slab Load = {W_fresh:.2f} kN/m²**")
            
            st.markdown("---")
            num_exist = st.number_input("Number of Existing Slabs Below", min_value=1, value=2, step=1, key=f'nx_{idx}')
            existing_slabs = []
            
            current_ui_transferred = W_fresh
            
            for j in range(int(num_exist)):
                st.markdown(f"#### Existing Slab {j+1}")
                ec1, ec2, ec3 = st.columns(3)
                ll_des = ec1.number_input("Design L.L (kN/m²)", value=2.50, step=0.5, key=f'll_{idx}_{j}')
                sidl_des = ec2.number_input("Design SIDL (kN/m²)", value=0.50, step=0.5, key=f'sidl_{idx}_{j}')
                strength = ec3.number_input("Strength Achieved (%)", value=80.0, step=5.0, key=f'str_{idx}_{j}')
                
                slab_capacity = (sidl_des + ll_des) * (strength / 100.0)
                next_ui_transferred = max(0, current_ui_transferred - slab_capacity)
                
                st.markdown(f"**Level {j+1} Back-propping Shoring (Props Under Existing Slab {j+1})**")
                ssc1, ssc2, ssc3 = st.columns(3)
                sys_j = ssc1.selectbox("Shoring Type", sys_opts, key=f'sj_{idx}_{j}')
                gx_j = ssc2.number_input("Grid X (m)", value=1.2, step=0.1, key=f'gxj_{idx}_{j}')
                gy_j = ssc3.number_input("Grid Y (m)", value=1.2, step=0.1, key=f'gyj_{idx}_{j}')
                
                subtype_j, unb_j, ext_j = "", 1.5, 3.0
                if sys_j == "Cup-lock":
                    subtype_j = ssc1.selectbox("Grade", ["S355 (st.52)", "S235"], key=f'cj_{idx}_{j}')
                    unb_j = ssc2.number_input("Unbraced (m)", value=1.5, key=f'cuj_{idx}_{j}')
                elif sys_j == "Ring-lock":
                    subtype_j = ssc1.selectbox("Size", ["Ringlock 1.5\"", "Ringlock 2.0\""], key=f'rj_{idx}_{j}')
                    unb_j = ssc2.number_input("Unbraced (m)", value=1.5, key=f'ruj_{idx}_{j}')
                elif sys_j == "Acrow Prop":
                    try: 
                        from config import PROP_DB
                        subtype_j = ssc1.selectbox("Prop Type", list(PROP_DB.keys()), key=f'pj_{idx}_{j}')
                    except: subtype_j = "Prop No.2"
                    ext_j = ssc2.number_input("Extension (m)", value=3.0, key=f'pej_{idx}_{j}')
                
                cap_j = get_shoring_capacity(sys_j, subtype_j, unb_j, ext_j)
                level_j_shore = {'sys': sys_j, 'gx': gx_j, 'gy': gy_j, 'area': gx_j*gy_j, 'cap': cap_j}
                
                if next_ui_transferred > 0:
                    actual_leg_load = (gx_j * gy_j) * next_ui_transferred
                    if actual_leg_load <= cap_j:
                        st.success(f"✅ **SAFE** | Load Transferred: **{next_ui_transferred:.2f} kN/m²** | Actual Leg Load: **{actual_leg_load:.2f} kN** < Leg Capacity: **{cap_j:.2f} kN**")
                    else:
                        st.error(f"❌ **UNSAFE** | Load Transferred: **{next_ui_transferred:.2f} kN/m²** | Actual Leg Load: **{actual_leg_load:.2f} kN** > Leg Capacity: **{cap_j:.2f} kN**")
                else:
                    st.success(f"✅ **NO SHORING REQUIRED** | Slab fully absorbed the load. (Transferred: 0.00 kN/m²)")
                
                current_ui_transferred = next_ui_transferred
                
                existing_slabs.append({
                    'll': ll_des, 'sidl': sidl_des, 'strength': strength, 'shore': level_j_shore
                })
                
            configs.append({
                'gamma_c': gamma_c, 'ts_fresh': ts_fresh, 'LL': LL_const, 'FW': FW_load, 'W_fresh': W_fresh,
                'existing_slabs': existing_slabs
            })
            
    st.markdown("---")
    if st.button("🚀 Calculate & Generate Detailed Left-Aligned Report", type="primary", use_container_width=True):
        
        for idx, conf in enumerate(configs):
            current_transferred = conf['W_fresh']
            levels_propped = 1 
            
            for slab in conf['existing_slabs']:
                if current_transferred <= 0: break
                capacity = (slab['sidl'] + slab['ll']) * (slab['strength'] / 100.0)
                next_transferred = max(0, current_transferred - capacity)
                if next_transferred > 0:
                    levels_propped += 1
                current_transferred = next_transferred
                
            conf['levels_propped'] = levels_propped
            conf['img_buf'] = plot_zone_system(conf)
            
            st.success(f"✅ **Zone {idx+1} Summary:** You need to prop **{levels_propped} Floors** in total. (1 Floor Main Shoring + {levels_propped-1} Floors Back-propping).")
            st.image(conf['img_buf'], use_container_width=False)
            
        docx_out = generate_backprop_report(configs, ref_code)
        st.download_button("⬇️ Download Back-propping Calculation Sheet", 
                           data=docx_out.getvalue(), 
                           file_name="Back_Propping_Report.docx", 
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
