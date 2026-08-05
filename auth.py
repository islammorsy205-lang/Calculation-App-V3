# auth.py

import streamlit as st
import pandas as pd
import gspread
import time
from streamlit.runtime.scriptrunner import get_script_run_ctx

@st.cache_resource
def get_sheet():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        sheet_url = creds_dict.pop("sheet_url", "")
        gc_auth = gspread.service_account_from_dict(creds_dict)
        sh = gc_auth.open_by_url(sheet_url)
        sheet = sh.sheet1
        if not sheet.get_values():
            sheet.append_row(["Email", "Name", "Status"])
        return sheet
    except Exception as e:
        st.error("❌ Database connection error. Please check Secrets configuration.")
        st.stop()

@st.cache_data(ttl=30)
def get_users_data():
    sheet = get_sheet()
    return sheet.get_all_records()

def check_access():
    try:
        if hasattr(st, "experimental_user") and st.experimental_user.email == "islam.morsy@acrow.co":
            st.session_state["access_granted"] = True
            st.session_state["is_admin"] = True
            return True
    except Exception:
        pass

    if st.query_params.get("admin") == "acrow_master":
        st.session_state["access_granted"] = True
        st.session_state["is_admin"] = True
        return True

    saved_user_email = st.query_params.get("user")
    if saved_user_email:
        saved_user_email = saved_user_email.strip().lower()
        if saved_user_email == "islam.morsy@acrow.co":
            st.session_state["access_granted"] = True
            st.session_state["is_admin"] = True
            return True
            
        records = get_users_data()
        if records:
            df = pd.DataFrame(records)
            if not df.empty and 'Email' in df.columns:
                df['Email_lower'] = df['Email'].astype(str).str.strip().str.lower()
                if saved_user_email in df['Email_lower'].values:
                    user_row = df[df['Email_lower'] == saved_user_email].iloc[0]
                    status = str(user_row.get('Status', '')).strip().lower()
                    if status == "approved":
                        st.session_state["access_granted"] = True
                        st.session_state["is_admin"] = False
                        st.session_state["user_name"] = str(user_row.get('Name', 'Eng.'))
                        return True

    if st.session_state.get("access_granted", False):
        return True

    st.markdown("<h2 style='text-align: center; color: #E10000; margin-top: 50px;'>🔒 Acrow Engineering System</h2>", unsafe_allow_html=True)
    st.markdown("<h4 style='text-align: center; color: #666;'>Please enter your Email to proceed</h4>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            email = st.text_input("Email:").strip().lower()
            name = st.text_input("Name (Optional):").strip()
            submitted = st.form_submit_button("Login / Request Access", use_container_width=True)
            
            if submitted:
                if email == "islam.morsy@acrow.co" or (email == "admin" and name == "acrow_master"):
                    st.query_params["admin"] = "acrow_master"
                    st.session_state["access_granted"] = True
                    st.session_state["is_admin"] = True
                    st.rerun()
                elif email:
                    records = get_users_data()
                    df = pd.DataFrame(records) if records else pd.DataFrame(columns=["Email", "Name", "Status"])
                    if not df.empty and 'Email' in df.columns:
                        df['Email_lower'] = df['Email'].astype(str).str.strip().str.lower()
                        if email in df['Email_lower'].values:
                            user_row = df[df['Email_lower'] == email].iloc[0]
                            status = str(user_row.get('Status', '')).strip().lower()
                            if status == "approved":
                                st.query_params["user"] = email
                                st.session_state["access_granted"] = True
                                st.session_state["is_admin"] = False
                                st.session_state["user_name"] = name if name else str(user_row.get('Name', 'Eng.'))
                                st.rerun()
                            elif status == "rejected":
                                st.error("❌ Access Denied. Your request has been rejected by the administrator.")
                            else:
                                st.warning("⏳ Your request is pending review. Please wait for approval.")
                        else:
                            sheet = get_sheet()
                            sheet.append_row([email, name if name else "Unknown", "Pending"])
                            get_users_data.clear() 
                            st.success("✅ Request sent successfully. Please contact the administrator for approval.")
                    else:
                        sheet = get_sheet()
                        sheet.append_row([email, name if name else "Unknown", "Pending"])
                        get_users_data.clear()
                        st.success("✅ Request sent successfully. Please contact the administrator for approval.")
                else:
                    st.error("Please enter your Email.")
    return False

def render_admin_dashboard():
    if st.session_state.get("is_admin", False):
        with st.expander("🛠️ Admin Dashboard (Access Requests)", expanded=False):
            records = get_users_data()
            if records:
                df = pd.DataFrame(records)
                col_t1, col_t2 = st.columns([2, 1])
                with col_t1:
                    st.dataframe(df, use_container_width=True)
                with col_t2:
                    target_email = st.selectbox("Select User Email:", df['Email'].unique())
                    new_status = st.selectbox("Set Access Status:", ["Approved", "Pending", "Rejected"])
                    if st.button("Update Status", type="primary"):
                        sheet = get_sheet()
                        row_idx = df.index[df['Email'] == target_email][0] + 2
                        sheet.update_cell(row_idx, 3, new_status)
                        get_users_data.clear() 
                        st.success(f"✅ Status for {target_email} updated successfully.")
                        time.sleep(1)
                        st.rerun()
            else:
                st.info("No users registered in the system yet.")

@st.cache_resource
def get_active_users_dict():
    return {}

def track_active_users():
    active_users = get_active_users_dict()
    ctx = get_script_run_ctx()
    if ctx:
        session_id = ctx.session_id
        active_users[session_id] = time.time()
    current_time = time.time()
    return sum(1 for uid, timestamp in active_users.items() if current_time - timestamp < 300)
