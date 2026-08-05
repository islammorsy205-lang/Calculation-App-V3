# helpers.py

import io
import re
from PIL import Image, ImageChops
import streamlit as st
import numpy as np

def crop_white_margins(img):
    if img.mode != "RGB":
        img = img.convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox:
        pad = 20
        bbox = (max(0, bbox[0]-pad), max(0, bbox[1]-pad), min(img.width, bbox[2]+pad), min(img.height, bbox[3]+pad))
        return img.crop(bbox)
    return img

def convert_transparent_to_pdf_stream(img_bytes):
    img_stream = io.BytesIO(img_bytes)
    img = Image.open(img_stream)
    bg = Image.new("RGB", img.size, (255, 255, 255))
    if img.mode in ('RGBA', 'LA'):
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img)
    pdf_stream = io.BytesIO()
    bg.save(pdf_stream, format='PDF')
    pdf_stream.seek(0)
    return pdf_stream

def get_val(key_base, i, default_val):
    if i == 0: 
        return default_val
    return st.session_state.get(f"{key_base}_0", default_val)

def get_idx(key_base, i, options_list, default_idx):
    if i == 0: 
        return default_idx
    val_0 = st.session_state.get(f"{key_base}_0")
    if val_0 in options_list: 
        return options_list.index(val_0)
    return default_idx

def extract_images_from_pdf(pdf_file):
    import fitz
    images = []
    try:
        pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
        for page_num in range(min(3, len(pdf_document))): 
            page = pdf_document.load_page(page_num)
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = pdf_document.extract_image(xref)
                images.append(base_image["image"])
        pdf_file.seek(0)
    except Exception:
        pass
    return images

def get_best_image_match(subject, system, image_list):
    if not image_list: 
        return 0
    best_idx, max_score = 0, -1
    stopwords = {"calculation", "sheet", "for", "system", "&", "and", "panel"}
    subj_words = [w.lower() for w in re.split(r'\W+', subject) if w and w.lower() not in stopwords]
    sys_words = [w.lower() for w in re.split(r'\W+', system) if w and w.lower() not in stopwords]
    keywords = set(subj_words + sys_words)

    for idx, img in enumerate(image_list):
        score = sum(1 for kw in keywords if kw in img.lower())
        if score > max_score: 
            max_score, best_idx = score, idx
    return best_idx

def get_strut_allowable(name, length):
    from config import STRUTS_DB
    data = STRUTS_DB.get(name)
    if not data: return 0.0
    if 'allow' in data: return data['allow']
    if 'pts' in data:
        pts = data['pts']
        keys = sorted(pts.keys())
        if length <= keys[0]: return pts[keys[0]]
        if length >= keys[-1]: return pts[keys[-1]]
        for i in range(len(keys)-1):
            k1, k2 = keys[i], keys[i+1]
            if k1 <= length <= k2:
                if k1 == k2: return pts[k1]
                return pts[k1] + (pts[k2] - pts[k1]) * (length - k1) / (k2 - k1)
    return 0.0

def get_valid_struts(req_len, mode="wind"):
    from config import STRUTS_DB
    valid = []
    for k, v in STRUTS_DB.items():
        if v['min'] <= req_len <= v['max']:
            if mode == "strongback":
                if "PPH" in k or "PPS" in k: 
                    valid.append(k)
            else:
                valid.append(k)
                
    if not valid: 
        return [f"No strut fits ({req_len:.2f}m)"]
    
    def sort_key(name):
        n_up = name.upper()
        score = 10
        
        if mode == "strongback":
            if "PPH" in n_up: score = 0
            elif "PPS" in n_up: score = 1
        else:
            if "TILT" in n_up or "MPP6" in n_up: score = 0
            elif "PPS" in n_up: score = 1
            elif "PPH" in n_up: score = 2
            
        # إعطاء أولوية ضعيفة جداً للأنواع المذكورة لإرجاعها للخلف
        if any(x in n_up for x in ["MPP1", "MPP2", "MPP3", "MPP4", "MPP5"]): 
            score += 20
        match = re.search(r'\d+', name.split()[0])
        if match and int(match.group()[-1]) in [1, 3, 5]: 
            score += 20
        if "X4" in n_up: 
            score += 20
            
        return (score, name)
    
    valid.sort(key=sort_key)
    return valid
