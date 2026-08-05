import streamlit as st
from docx import Document
from docx.shared import Inches
import fitz  # PyMuPDF
import io
import os

# توسيع عرض الصفحة لتستوعب الأعمدة بشكل جيد
st.set_page_config(layout="wide", page_title="Formwork Calculation Sheet")

st.title("مُجمّع النوتة الحسابية - Formwork Calculation Sheet")

# القائمة الدقيقة بأسماء الملفات مطابقة للصورة المرفقة تماماً
pdf_files = [
    "Data Sheet of PPS102.pdf",
    "Data Sheet of Formwork Material.pdf",
    "Data Sheet for weld Resistance at Steel Ladder.pdf",
    "Data Sheet for VMC.pdf",
    "Data Sheet for U head jack 5 ton.pdf",
    "Data Sheet for Tilt up shore.pdf",
    "Data Sheet for Tie rod 15mm.pdf",
    "Data Sheet for Soldier.pdf",
    "Data Sheet for Shorebrace Frame.pdf",
    "Data Sheet for Scaffolding tube.pdf",
    "Data Sheet for Rivet pin.pdf",
    "Data Sheet for Ringlock vertical 1.5''.pdf",
    "Data Sheet for PVC Board.pdf",
    "Data Sheet for Post head jack & U-head jack.pdf",
    "Data Sheet for Plywood.pdf",
    "Data Sheet for OSHA LVL TIMBER BOARD.pdf",
    "Data Sheet for Lifting Eye bolt.pdf",
    "Data Sheet for Hex Nut with Welded Bars.pdf",
    "Data Sheet for H20.pdf",
    "Data Sheet for ECO-form Crane Hook.pdf",
    "Data Sheet for Eco Prop 350.pdf",
    "Data Sheet for Crane Splice H20.pdf",
    "Data Sheet for Bolt M16×80 mm.pdf",
    "Data Sheet for Aluminium Beam.pdf",
    "DATA SHEET FOR ACROW BOLT.pdf",
    "Data Sheet for Acrow bolt 5 cm.pdf"
]

st.subheader("Select Data Sheets to Include:")

# تقسيم الخيارات إلى 3 أعمدة ليكون شكل الصفحة منظماً
cols = st.columns(3)
selected_pdfs = {}

for i, pdf in enumerate(pdf_files):
    # إزالة كلمة .pdf من العرض فقط ليكون الشكل أفضل، لكن سيتم استدعاء الملف باسمه الكامل
    display_name = pdf.replace(".pdf", "")
    with cols[i % 3]:
        selected_pdfs[pdf] = st.checkbox(display_name)

st.divider()

# رفع ملف الحسابات الأساسي
uploaded_calc_sheet = st.file_uploader("Upload Main Calculation Sheet (Word - .docx)", type=["docx"])

if st.button("Generate Final Word Document"):
    if uploaded_calc_sheet:
        doc = Document(uploaded_calc_sheet)
        doc.add_page_break()
        doc.add_heading('Attached Data Sheets', level=1)

        def append_pdf_to_word(pdf_name, doc_obj):
            # التأكد من وجود الملف في المجلد لتجنب توقف الموقع
            if not os.path.exists(pdf_name):
                st.warning(f"⚠️ الملف غير موجود: {pdf_name}. يرجى التأكد من رفعه على GitHub.")
                return
            
            pdf_document = fitz.open(pdf_name)
            for page_num in range(len(pdf_document)):
                page = pdf_document.load_page(page_num)
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                image_stream = io.BytesIO(img_bytes)
                doc_obj.add_picture(image_stream, width=Inches(6.0))

        # دمج الملفات التي تم تحديدها (عمل علامة صح عليها) فقط
        for pdf, is_selected in selected_pdfs.items():
            if is_selected:
                append_pdf_to_word(pdf, doc)

        output_stream = io.BytesIO()
        doc.save(output_stream)
        
        st.success("✅ تم تجميع النوتة الحسابية بنجاح!")
        st.download_button(
            label="⬇️ Download Final Calculation Sheet",
            data=output_stream.getvalue(),
            file_name="Final_Calculation_Sheet.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    else:
        st.error("❌ يرجى رفع ملف الـ Word الأساسي (Calculation Sheet) أولاً.")