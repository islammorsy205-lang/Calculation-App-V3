# ui_project.py

import os
import fitz
import streamlit as st
from datetime import date
from helpers import get_best_image_match
from config import SHORING_OPTIONS_SLAB, SECTIONS_DB

def render_project_details():
    st.subheader("1. Project Details & References")

    col1, col2, col3 = st.columns(3)

    with col1: 
        project_name = st.text_input("Main Project Name", "Acrow Mega Project")
        contractor = st.text_input("Client / Contractor", "Main Contractor")

    with col2: 
        # الجملة بالكامل تظهر في الواجهة ويمكن للمستخدم تعديل الكلمة الأخيرة فقط براحته
        calc_subject = st.text_input("Structure Element", "CALCULATION SHEET FOR SOLID SLAB")
        
        system_name = st.selectbox(
            "Formwork System Name", 
            SHORING_OPTIONS_SLAB + [
                "Timber H20 & Soldier System", 
                "Acrow Beam S12 & Soldier System", 
                "Eco-form Panel System", 
                "Tech-form Panel System", 
                "Curved Steel Panel System", 
                "Circular Steel Panel System"
            ]
        )

    with col3: 
        proj_no = st.text_input("Project No.", "PRJ-2026")
        
        # اللوجيك المخفي لسحب الإيميل وتوليد اختصار الاسم (Calculated By)
        # أضفت بحث في أكتر من اسم متغير احتياطياً
        user_email = st.session_state.get('user_email') or st.session_state.get('email') or st.session_state.get('username') or ""
        
        calc_by_initials = ""
        if isinstance(user_email, str) and '@' in user_email:
            name_part = user_email.split('@')[0]
            parts = name_part.split('.')
            if len(parts) >= 2:
                # سحب أول حرف من أول مقطعين وتكبيرهم (مثال: I.M)
                calc_by_initials = f"{parts[0][0].upper()}.{parts[1][0].upper()}"
            elif len(parts) == 1:
                # في حالة وجود اسم واحد فقط قبل علامة الـ @
                calc_by_initials = f"{parts[0][0].upper()}"
        
        # دمج الاختصار مع اللقب (مثال: Eng. I.M)
        if calc_by_initials:
            calc_by = f"Eng. {calc_by_initials}"
        else:
            calc_by = "Eng."
            
        # الخانة تم إخفاؤها من الواجهة تماماً وتعمل في الخلفية لترسل المتغير calc_by للطباعة
        
    date_val = date.today().strftime("%d/%m/%Y")
    chk_by = "Eng. M.F."

    st.markdown("**Select Design Code, Cover Image & Method Statements:**")
    col_r1, col_r2, col_r3 = st.columns(3)

    with col_r1: 
        ref_code = st.selectbox("Design Codes & References:", ["British Standard (BS)", "American Code (ACI)", "None"])

    with col_r2:
        av_img = [f for f in os.listdir('.') if f.lower().endswith(('.jpg', '.jpeg', '.png')) and not f.startswith('ref_')]
        best_img_idx = get_best_image_match(calc_subject, system_name, av_img) if av_img else 0
        cover_img = st.selectbox("Cover Page Image:", av_img if av_img else ["No images found."], index=best_img_idx)

    with col_r3:
        av_ds = [f for f in os.listdir('.') if f.lower().endswith('.pdf') and f.lower().startswith('data sheet')]
        c1, c2 = st.columns([10, 1])
        with c1: 
            data_sheets = st.multiselect("Select Data Sheets:", av_ds)
        with c2:
            st.markdown("<div style='margin-top: 29px;'></div>", unsafe_allow_html=True)
            if data_sheets:
                merged_ds = fitz.open()
                for pdf_file in data_sheets:
                    with fitz.open(pdf_file) as doc_pdf: 
                        merged_ds.insert_pdf(doc_pdf)
                st.download_button("⬇️", data=merged_ds.write(), file_name="Data_Sheets.pdf", mime="application/pdf")
                merged_ds.close()

    def_sec, def_main = 0, 2 
    for f in data_sheets:
        if "h20" in f.lower(): 
            def_sec = list(SECTIONS_DB.keys()).index("Timber H20")
        if "soldier" in f.lower(): 
            def_main = list(SECTIONS_DB.keys()).index("Soldier U100")
            
    return project_name, contractor, calc_subject, system_name, proj_no, calc_by, date_val, chk_by, ref_code, cover_img, data_sheets, def_sec, def_main
