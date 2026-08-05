# bridge_master.py

import streamlit as st
import numpy as np
import pandas as pd
import io
import os
import ast
import re
import matplotlib
matplotlib.use('Agg') # 💡 وضع الخوادم لمنع أي انهيار للواجهة
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# =========================================================
# 1. Native HTML Parser Engine (Extracting EVERYTHING Safely)
# =========================================================
def parse_bridge_html_native(html_content):
    """يقرأ مصفوفات الجافاسكريبت وجداول الـ HTML بنظام الاستخراج النصي الآمن تماماً لتجنب أعطال الذاكرة"""
    
    # 1. Extract Arrays using pure string finding
    def extract_array_str(var_name):
        start_marker = f"const {var_name}"
        idx_start = html_content.find(start_marker)
        if idx_start == -1: return "[]"
        idx_bracket = html_content.find("[", idx_start)
        idx_end = html_content.find("];", idx_bracket)
        if idx_bracket == -1 or idx_end == -1: return "[]"
        return html_content[idx_bracket : idx_end + 1]

    nodes_raw = extract_array_str("globalNodes")
    elems_raw = extract_array_str("globalElements")
    
    nodes, elements = None, None
    def clean_js_to_python(js_str):
        js_str = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', js_str)
        js_str = js_str.replace(': false', ': False').replace(': true', ': True')
        js_str = js_str.replace(':false', ':False').replace(':true', ':True')
        return js_str
        
    try:
        nodes = ast.literal_eval(clean_js_to_python(nodes_raw))
        elements = ast.literal_eval(clean_js_to_python(elems_raw))
    except Exception as e:
        st.error(f"⚠️ خطأ في قراءة مصفوفات النموذج: {e}")

    # 2. Extract Tables Natively (Bypassing Pandas to prevent Crashes)
    tables = []
    table_blocks = re.findall(r'<table.*?>(.*?)</table>', html_content, re.DOTALL | re.IGNORECASE)
    
    for block in table_blocks:
        rows = re.findall(r'<tr.*?>(.*?)</tr>', block, re.DOTALL | re.IGNORECASE)
        parsed_table = []
        for row in rows:
            cells = re.findall(r'<(t[hd]).*?>(.*?)</\1>', row, re.DOTALL | re.IGNORECASE)
            clean_cells = [re.sub(r'<.*?>', '', c[1]).strip() for c in cells]
            if clean_cells:
                parsed_table.append(clean_cells)
        if parsed_table:
            tables.append(parsed_table)

    return nodes, elements, tables

# =========================================================
# 2. SAP2000 Plotting Engine (All Diagrams & Styles)
# =========================================================
def safe_render_fig(fig):
    try:
        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=200, bbox_inches='tight', transparent=True)
        return buf.getvalue()
    finally:
        plt.close(fig)

def draw_base_structure(ax, nodes, elements):
    nodes_dict = {n['id']: n for n in nodes}
    for el in elements:
        n1, n2 = nodes_dict.get(el['i']), nodes_dict.get(el['j'])
        if not n1 or not n2: continue
        ax.plot([float(n1['x']), float(n2['x'])], [float(n1['y']), float(n2['y'])], color='dimgray', linewidth=1.5, zorder=1)
        
    for n in nodes:
        if n.get('fixX') or n.get('fixY') or n.get('fixT'):
            x, y = float(n['x']), float(n['y'])
            t = 'Fixed' if (n.get('fixX') and n.get('fixY') and n.get('fixT')) else \
                'Hinged' if (n.get('fixX') and n.get('fixY')) else 'Roller'
                
            if t == 'Hinged' or t == 'Fixed':
                h, w = 0.5, 0.4
                p1, p2, p3 = (x, y), (x + w/2, y - h), (x - w/2, y - h)
                ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.5, zorder=5))
                ax.plot([x - w, x + w], [y - h, y - h], color='limegreen', lw=2.0, zorder=4)
            elif t == 'Roller':
                h, w, r = 0.4, 0.3, 0.12
                p1, p2, p3 = (x, y), (x + w/2, y - h), (x - w/2, y - h)
                ax.add_patch(Polygon([p1, p2, p3], facecolor='none', edgecolor='limegreen', lw=1.5, zorder=5))
                ax.add_patch(plt.Circle((x, y - h - r), r, facecolor='none', edgecolor='limegreen', lw=1.5, zorder=5))
                ax.plot([x - 0.2, x + 0.2], [y - h - 2*r, y - h - 2*r], color='limegreen', lw=2.0, zorder=4)
    return nodes_dict

def draw_sap2000_forces(val_key, nodes, elements, scale, is_axial=False):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    
    nodes_dict = draw_base_structure(ax, nodes, elements)

    for el in elements:
        n1, n2 = nodes_dict.get(el['i']), nodes_dict.get(el['j'])
        if not n1 or not n2: continue
        
        x1, y1 = float(n1['x']), float(n1['y'])
        x2, y2 = float(n2['x']), float(n2['y'])
        dx, dy = x2 - x1, y2 - y1
        L_s = np.hypot(dx, dy)
        if L_s < 1e-5: continue
        
        c, s = dx/L_s, dy/L_s
        diag_arr = el.get('axialDiag' if is_axial else 'diag', [])
        if not diag_arr: continue
        
        ts = np.array([float(pt.get('t', 0)) for pt in diag_arr])
        vals_orig = np.array([float(pt.get('n' if is_axial else val_key.lower(), 0)) for pt in diag_arr])
        if len(vals_orig) == 0: continue
        
        plot_vals = -vals_orig if val_key != 'N' else vals_orig
        
        px_arr = x1 + c * (ts * L_s) - s * plot_vals * scale
        py_arr = y1 + s * (ts * L_s) + c * plot_vals * scale
        
        color_pos, color_neg = 'blue', 'red'
        
        ax.plot([x1, px_arr[0]], [y1, py_arr[0]], color=color_pos if vals_orig[0] >= 0 else color_neg, linewidth=0.8)
        for k in range(len(px_arr)-1):
            avg_v = (vals_orig[k] + vals_orig[k+1]) / 2.0
            seg_color = color_pos if avg_v >= 0 else color_neg
            ax.plot([px_arr[k], px_arr[k+1]], [py_arr[k], py_arr[k+1]], color=seg_color, linewidth=0.8)
        ax.plot([px_arr[-1], x2], [py_arr[-1], y2], color=color_pos if vals_orig[-1] >= 0 else color_neg, linewidth=0.8)
        
        num_lines = max(2, int(L_s / 0.4))
        for i in range(1, num_lines):
            frac = i / num_lines
            lx, ly = x1 + frac * dx, y1 + frac * dy
            idx_val = int(frac * (len(plot_vals)-1))
            lv = plot_vals[idx_val]
            ax.plot([lx, lx - s * lv * scale], [ly, ly + c * lv * scale], color=color_pos if vals_orig[idx_val] >= 0 else color_neg, linewidth=0.3, alpha=0.6)
            
        max_idx = np.argmax(np.abs(vals_orig))
        if abs(vals_orig[max_idx]) > 0.1:
            ax.text(px_arr[max_idx] - s*0.4, py_arr[max_idx] + c*0.4, f"{abs(vals_orig[max_idx]):.1f}", color='black', fontsize=6, ha='center', va='center')

    return safe_render_fig(fig)

def draw_sap2000_deflection(nodes, elements, defl_scale):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    
    nodes_dict = draw_base_structure(ax, nodes, elements)
    max_defl = 0
    max_pt = None
    
    for el in elements:
        n1, n2 = nodes_dict.get(el['i']), nodes_dict.get(el['j'])
        if not n1 or not n2: continue
        x1 = float(n1['x']) + float(n1.get('dx', 0)) * defl_scale
        y1 = float(n1['y']) + float(n1.get('dy', 0)) * defl_scale
        x2 = float(n2['x']) + float(n2.get('dx', 0)) * defl_scale
        y2 = float(n2['y']) + float(n2.get('dy', 0)) * defl_scale
        ax.plot([x1, x2], [y1, y2], color='red', linestyle='--', linewidth=1.5, alpha=0.8, zorder=3)
        
    for n in nodes:
        dx, dy = float(n.get('dx', 0)), float(n.get('dy', 0))
        defl = np.hypot(dx, dy)
        if defl > max_defl:
            max_defl = defl
            max_pt = (float(n['x']) + dx * defl_scale, float(n['y']) + dy * defl_scale)
            
    if max_pt and max_defl > 0.0001:
        ax.annotate(f"Max Defl: {max_defl*1000:.2f} mm", xy=max_pt, xytext=(max_pt[0]+1, max_pt[1]+1),
                    arrowprops=dict(facecolor='red', shrink=0.05, width=1.5, headwidth=6),
                    fontsize=8, color='red', fontweight='bold', zorder=10)

    return safe_render_fig(fig)

def draw_sap2000_reactions(nodes, elements):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    draw_base_structure(ax, nodes, elements)
    
    for n in nodes:
        rx, ry = float(n.get('rx', 0)), float(n.get('ry', 0))
        x, y = float(n['x']), float(n['y'])
        arr_len = 1.2
        if abs(ry) > 0.1:
            dy = -arr_len if ry > 0 else arr_len
            ax.arrow(x, y + dy, 0, -dy*0.8, head_width=0.3, head_length=0.4, fc='darkorange', ec='darkorange', lw=1.5, zorder=6)
            ax.text(x, y + dy - np.sign(dy)*0.3, f"{abs(ry):.1f}", color='black', fontsize=7, fontweight='bold', ha='center')
        if abs(rx) > 0.1:
            dx = -arr_len if rx > 0 else arr_len
            ax.arrow(x + dx, y, -dx*0.8, 0, head_width=0.3, head_length=0.4, fc='darkorange', ec='darkorange', lw=1.5, zorder=6)
            ax.text(x + dx - np.sign(dx)*0.3, y, f"{abs(rx):.1f}", color='black', fontsize=7, fontweight='bold', va='center')

    return safe_render_fig(fig)

def draw_sap2000_loads(nodes, elements):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_aspect('equal', adjustable='datalim')
    ax.axis('off')
    nodes_dict = draw_base_structure(ax, nodes, elements)
    max_w = max([abs(float(el.get('wTotal', 0))) for el in elements] + [1])
    scale_h = 1.5 / max_w
    
    for el in elements:
        w = float(el.get('wTotal', 0))
        if abs(w) < 0.1: continue
        n1, n2 = nodes_dict.get(el['i']), nodes_dict.get(el['j'])
        if not n1 or not n2: continue
        x1, y1 = float(n1['x']), float(n1['y'])
        x2, y2 = float(n2['x']), float(n2['y'])
        h = abs(w) * scale_h
        
        ax.add_patch(Polygon([(x1,y1), (x1, y1+h), (x2, y2+h), (x2, y2)], facecolor='royalblue', edgecolor='blue', alpha=0.3, zorder=2))
        num_arr = max(1, int(np.hypot(x2-x1, y2-y1) / 0.5))
        for i in range(1, num_arr):
            fx, fy = x1 + (x2-x1) * (i/num_arr), y1 + (y2-y1) * (i/num_arr)
            ax.arrow(fx, fy+h, 0, -h*0.8, head_width=0.1, head_length=0.2, fc='blue', ec='blue', lw=0.5, zorder=3)
        ax.text((x1+x2)/2, (y1+y2)/2 + h + 0.3, f"{abs(w):.2f}", color='blue', fontsize=7, fontweight='bold', ha='center')

    return safe_render_fig(fig)

# =========================================================
# 3. Comprehensive Report Generator (Word Flow)
# =========================================================
def generate_comprehensive_bridge_report(nodes, elements, tables, img_bytes_dict, proj_info):
    
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
        
    def add_line(text, bold=False, color=None, size=11):
        p = doc.add_paragraph()
        force_ltr_left(p)
        p.paragraph_format.line_spacing = 1.5
        r = p.add_run(text)
        r.font.name = 'Arial'
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.rtl = False
        if color: r.font.color.rgb = color
        return p

    def add_native_table_to_word(doc, table_data, title):
        if not table_data or len(table_data) < 2: return
        add_line(title, bold=True, size=13, color=RGBColor(0,0,128))
        
        cols_count = len(table_data[0])
        table = doc.add_table(rows=len(table_data), cols=cols_count)
        
        # 💡 الحل الجذري هنا لتخطي خطأ הستايل
        try:
            table.style = 'Table Grid'
        except Exception:
            try:
                table.style = 'TableGrid'
            except Exception:
                pass # تجاهل الستايل واستخدام الافتراضي
        
        for r_idx, row_data in enumerate(table_data):
            row_cells = table.rows[r_idx].cells
            for c_idx, cell_text in enumerate(row_data):
                if c_idx < cols_count:
                    row_cells[c_idx].text = str(cell_text)
                    for paragraph in row_cells[c_idx].paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(8)
                            if r_idx == 0:
                                run.font.bold = True
                            else:
                                text_up = str(cell_text).upper()
                                if "PASS" in text_up or "SAFE" in text_up:
                                    run.font.color.rgb = RGBColor(0, 128, 0)
                                    run.font.bold = True
                                elif "FAIL" in text_up or "UNSAFE" in text_up:
                                    run.font.color.rgb = RGBColor(255, 0, 0)
                                    run.font.bold = True
        doc.add_paragraph()

    # --- Cover Page Replacements ---
    def remove_hardcoded_prefix(p):
        if p.text and "CALCULATION SHEET FOR" in p.text.upper():
            clean_text = re.sub(r'(?i)CALCULATION SHEET FOR\s*', '', p.text)
            if p.runs:
                f_name, f_size, f_bold, f_color = p.runs[0].font.name, p.runs[0].font.size, p.runs[0].font.bold, p.runs[0].font.color.rgb if p.runs[0].font.color else None
                for r in p.runs: r.text = ""
                p.runs[0].text = clean_text
                p.runs[0].font.name, p.runs[0].font.size, p.runs[0].font.bold = f_name, f_size, f_bold
                if f_color: p.runs[0].font.color.rgb = f_color
            else:
                p.text = clean_text

    for p in doc.paragraphs: remove_hardcoded_prefix(p)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs: remove_hardcoded_prefix(p)

    replacements = {
        "[PROJECT_NAME]": proj_info.get('proj_name', ''),
        "[CONTRACTOR]": proj_info.get('contractor', ''),
        "[CALC_SUBJECT]": proj_info.get('calc_sub', ''),
        "[SYSTEM_NAME]": proj_info.get('sys_name', '')
    }
    
    cover_img = proj_info.get('cover_img')
    for p in doc.paragraphs:
        if "[COVER_IMAGE]" in p.text:
            for r in p.runs: r.text = r.text.replace("[COVER_IMAGE]", "")
            if cover_img and cover_img != "No images found." and os.path.exists(cover_img):
                p.add_run().add_picture(cover_img, width=Cm(15.0))
        for k, v in replacements.items():
            if k in p.text:
                for r in p.runs: r.text = r.text.replace(k, str(v))
                if k in p.text: p.text = p.text.replace(k, str(v))

    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for k, v in replacements.items():
                        if k in p.text:
                            for r in p.runs: r.text = r.text.replace(k, str(v))
                            if k in p.text: p.text = p.text.replace(k, str(v))
                                
    doc.add_page_break()
    
    # --- Headers and Footers ---
    for sec in doc.sections:
        for hf in [sec.header, sec.first_page_header, sec.footer, sec.first_page_footer]:
            if hf:
                for tbl in hf.tables:
                    for row in tbl.rows:
                        for cell in row.cells:
                            for p in cell.paragraphs:
                                for k, v in {"[PROJECT_NAME]": proj_info.get('proj_name',''), "[CONTRACTOR]": proj_info.get('contractor',''), "[PROJ_NO]": proj_info.get('proj_no',''), "[DATE]": proj_info.get('date_val',''), "[CALC_BY]": proj_info.get('calc_by',''), "[CHK_BY]": proj_info.get('chk_by',''), "[REV]": "00"}.items():
                                    if k in p.text:
                                        for r in p.runs: r.text = r.text.replace(k, str(v))
                                        if k in p.text: p.text = p.text.replace(k, str(v))
                                        for r in p.runs: r.font._element.set(qn('w:ascii'), 'Arial')
    
    # --- Regulations & Standards ---
    insert_blue_banner(doc, "REGULATIONS AND STANDARDS", font_size=16)
    doc.add_paragraph()
    ref_code = proj_info.get('ref_code', 'BS')
    if "BS" in ref_code: 
        add_eq(doc, "1- BS 5975-1996: FORMWORK FOR CONCRETE")
        add_eq(doc, "2- BS 5975-2008: FORMWORK FOR CONCRETE")
        add_eq(doc, "3- FORMWORK AGUIDE TO AGOOD PRATICE")
        add_eq(doc, "4- WISA®-FORM PLYWOOD.")
        add_eq(doc, "5- THE SAUDI BUILDING CODE (SBC) 2018")
    else: 
        add_eq(doc, "1- ACI 347R-14 ....... GUIDE TO FORMWORK FOR CONCRETE.")
        add_eq(doc, "2- ACI SP-4 ......... FORMWORK FOR CONCRETE.")
        add_eq(doc, "3- WISA®-FORM PLYWOOD.")
        add_eq(doc, "4- THE SAUDI BUILDING CODE (SBC) 2018")
    
    # --- Data Sheets ---
    data_sheets = proj_info.get('data_sheets', [])
    if data_sheets:
        doc.add_page_break()
        insert_blue_banner(doc, "FORMWORK MATERIALS TECHNICAL DATA", font_size=14)
        for f in data_sheets:
            if os.path.exists(f): 
                append_pdf_stream_to_word(f, doc, is_path=True, max_width_cm=17.5, max_height_cm=24.0, add_border=True, reduce_first_page=True)
    
    # --- Design Loads ---
    design_pdf = "Design_Loads_BS.pdf" if "BS" in ref_code and os.path.exists("Design_Loads_BS.pdf") else ("Design_Loads_ACI.pdf" if "ACI" in ref_code and os.path.exists("Design_Loads_ACI.pdf") else None)
    if design_pdf: 
        doc.add_page_break()
        insert_blue_banner(doc, "DESIGN LOADS FOR SLAB", font_size=14)
        append_pdf_stream_to_word(design_pdf, doc, is_path=True, max_width_cm=17.5, max_height_cm=24.0, add_border=True, reduce_first_page=True)
        
    # --- Formwork Sketches Banner ---
    doc.add_page_break()
    insert_blue_banner(doc, "FORMWORK SKETCHES", font_size=16)

    # --- Extracted Bridge Content ---
    doc.add_page_break()
    add_line("BRIDGE FORMWORK DESIGN DATA (EXTRACTED)", bold=True, size=14)
    doc.add_paragraph()
    
    # 1. Extracted Tables
    table_titles = [
        "Nodal Displacements", "Element Internal Forces Summary", "BMD Extreme Values",
        "SFD Extreme Values", "Axial Force (Main Members)", "Axial Force (Bracing)",
        "Deflection Check", "Support Reactions Summary", "Applied Loads"
    ]
    
    for i, table_data in enumerate(tables):
        if not table_data or len(table_data) < 2: continue
        title = table_titles[i] if i < len(table_titles) else f"Data Table {i+1}"
        add_native_table_to_word(doc, table_data, f"Table {i+1}: {title}")

    # 2. Diagrams in Specific Order matching Images
    doc.add_page_break()
    
    # Red Header Helper
    def add_red_underlined_header(text):
        p = doc.add_paragraph()
        force_ltr_left(p)
        r = p.add_run(text)
        r.font.name = 'Arial'
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.underline = True
        r.font.color.rgb = RGBColor(255, 0, 0)
        return p

    diagram_order = [
        ('L', "LOAD DISTRIBUTION DIAGRAM FOR LOADS (KN/m'):"),
        ('M', "BENDING MOMENT DIAGRAM DUE TO (DL+LL) (KN.m):"),
        ('N', "NORMAL FORCE DIAGRAM DUE TO (DL+LL) (KN):"),
        ('V', "SHEAR FORCE DIAGRAM DUE TO (DL+LL) (KN):"),
        ('D', "DEFLECTION SHAPE DIAGRAM (mm):"),
        ('R', "SHOREBRACE REACTIONS :")
    ]
    
    for key, title in diagram_order:
        img_bytes = img_bytes_dict.get(key)
        if not img_bytes: continue
        
        add_red_underlined_header(title)
        
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.add_run().add_picture(io.BytesIO(img_bytes), width=Cm(16.5))
        doc.add_paragraph()

    out = io.BytesIO()
    doc.save(out)
    return out

# =========================================================
# 4. Main UI Module
# =========================================================
def render_bridge_module(proj_info):
    st.markdown("## 🌉 Bridge Formwork & Structures (Advanced FEA Extractor)")
    st.info("💡 **Smart Extractor:** Upload your Acrow Bridge HTML Report to extract all calculations, safety checks, and diagrams. Convert them instantly to SAP2000 style and generate a complete Word calculation sheet.")
    
    uploaded_file = st.file_uploader("📂 Upload Acrow Bridge FEA HTML File", type=["html"])
    
    if uploaded_file is not None:
        html_content = uploaded_file.getvalue().decode("utf-8")
        
        with st.spinner("Extracting Nodes, Elements, and Checking Tables safely..."):
            nodes, elements, tables = parse_bridge_html_native(html_content)
            
        if nodes and elements:
            st.success(f"✅ Data Extracted Successfully! Found **{len(nodes)} Nodes**, **{len(elements)} Elements**, and **{len(tables)} Analysis Tables**.")
            st.markdown("---")
            
            diag_placeholder = st.empty()
            
            st.markdown("### 🎛️ Customize Diagram Scales")
            c_s1, c_s2, c_s3, c_s4 = st.columns(4)
            sc_n = c_s1.slider("Axial Scale", 0.001, 0.050, 0.015, step=0.001)
            sc_v = c_s2.slider("Shear Scale", 0.001, 0.050, 0.015, step=0.001)
            sc_m = c_s3.slider("Moment Scale", 0.001, 0.100, 0.015, step=0.001)
            sc_d = c_s4.slider("Deflection Scale", 1.0, 100.0, 20.0, step=1.0)
            
            img_bufs = {}
            with diag_placeholder.container():
                st.markdown("### 🎛️ Live SAP2000-Style Diagrams")
                try:
                    img_bufs = {
                        'L': draw_sap2000_loads(nodes, elements),
                        'N': draw_sap2000_forces('N', nodes, elements, sc_n, is_axial=True),
                        'V': draw_sap2000_forces('V', nodes, elements, sc_v),
                        'M': draw_sap2000_forces('M', nodes, elements, sc_m),
                        'D': draw_sap2000_deflection(nodes, elements, sc_d),
                        'R': draw_sap2000_reactions(nodes, elements)
                    }
                    
                    st.image(img_bufs['L'], caption="Applied Loads Diagram")
                    c_p1, c_p2 = st.columns(2)
                    c_p1.image(img_bufs['M'], caption="Bending Moment Diagram (kN.m)")
                    c_p2.image(img_bufs['V'], caption="Shear Force Diagram (kN)")
                    c_p3, c_p4 = st.columns(2)
                    c_p3.image(img_bufs['N'], caption="Axial Force Diagram (kN)")
                    c_p4.image(img_bufs['D'], caption="Deflection Deformed Shape")
                    st.image(img_bufs['R'], caption="Support Reactions Diagram")
                except Exception as e:
                    st.error(f"❌ حدث خطأ أثناء الرسم: {e}")
                
            st.markdown("---")
            
            if img_bufs:
                if st.button("🚀 Process & Generate Calculation Sheet", type="primary", use_container_width=True):
                    with st.spinner("Building Comprehensive Word Document..."):
                        try:
                            # 💡 نمرر هنا بيانات المشروع (proj_info) لبناء النوتة الكاملة
                            docx_out = generate_comprehensive_bridge_report(nodes, elements, tables, img_bufs, proj_info)
                            st.session_state['bridge_docx_bytes'] = docx_out.getvalue()
                            st.success("✅ Document Ready! You can download it below.")
                        except Exception as e:
                            st.error(f"⚠️ خطأ أثناء تجميع ملف الوورد: {e}")
                
                if 'bridge_docx_bytes' in st.session_state:
                    st.download_button(
                        "⬇️ Download Full Bridge Calculation Sheet (Word)", 
                        data=st.session_state['bridge_docx_bytes'], 
                        file_name="Acrow_Bridge_Full_Report.docx", 
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
