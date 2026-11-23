df = df.rename(columns={
        "id": "ID", "created_at": "Date / Time", "product_name": "Product",
        "kind": "Type", "quantity": "Quantity", "price": "Unit Price (RM)",
        "note": "Supplier", "username": "User"
    })

    st.markdown("### Filter and Export")
    date_values = df["Date / Time"].dropna()
    if not date_values.empty:
        min_date, max_date = date_values.min().date(), date_values.max().date()
    else:
        min_date = max_date = pd.Timestamp.now().date()

    date_col1, date_col2 = st.columns(2)
    start_date = date_col1.date_input("Start date", value=min_date, min_value=min_date,
                                      max_value=max_date, key="history_start_date")
    end_date = date_col2.date_input("End date", value=max_date, min_value=min_date,
                                    max_value=max_date, key="history_end_date")
    if start_date > end_date:
        st.error("Start date cannot be later than end date.")
        return

    c1, c2 = st.columns([2, 1])
    search = c1.text_input("Filter by product or user", key="history_search").strip().lower()
    kind = c2.selectbox("Movement type", ["All", "Incoming", "Outgoing"], key="history_type")

    filtered = df[
        df["Date / Time"].notna()
        & (df["Date / Time"].dt.date >= start_date)
        & (df["Date / Time"].dt.date <= end_date)
    ].copy()
    if search:
        filtered = filtered[
            filtered["Product"].astype(str).str.lower().str.contains(search, na=False)
            | filtered["User"].astype(str).str.lower().str.contains(search, na=False)
        ]
    if kind != "All":
        filtered = filtered[filtered["Type"] == kind]

    st.caption(f"Showing {len(filtered)} transaction(s) from {start_date} to {end_date}.")
    display_df = filtered.copy()
    display_df["Date / Time"] = display_df["Date / Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # 下载文件会遵循当前日期、产品/用户关键字及交易类型筛选条件。
    csv_df = filtered.copy()
    csv_df["Date / Time"] = csv_df["Date / Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    st.download_button(
        "⬇️ Download filtered transactions (CSV)",
        data=csv_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"transaction_history_{start_date}_{end_date}.csv",
        mime="text/csv",
        use_container_width=True
    )


def password_page():
    st.subheader("Change Password")
    with st.form("change_password_form", clear_on_submit=True):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password (at least 8 characters)", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submit = st.form_submit_button("Update password", type="primary")
    if submit:
        if new != confirm:
            st.error("The new passwords do not match.")
        else:
            ok, msg = change_password(st.session_state.user["username"], old, new)
            st.success(msg) if ok else st.error(msg)


# 【主程序入口】设置页面样式、检查云端数据库、处理登录状态、建立侧边导航并显示对应页面。
def main():
    # 【网页外观】设置主页面及侧边栏背景颜色，并统一按钮圆角样式。
    st.markdown("""<style>
    .stApp {background:#f4f7fb;} [data-testid="stSidebar"] {background:#eaf0f8;}
    div.stButton>button, div.stFormSubmitButton>button {border-radius:9px;}
    </style>""", unsafe_allow_html=True)
    try:
        db_client()  # fail early with a clear message if cloud DB settings are missing
    except Exception as e:
        st.error("Cloud database is not configured. Add SUPABASE_URL and SUPABASE_KEY in Streamlit app Secrets.")
        st.code(str(e))
        st.stop()

    if "user" not in st.session_state:
        st.session_state.user = None
    if "page_nav" not in st.session_state:
        st.session_state.page_nav = "dashboard"
    if not st.session_state.user:
        login_screen()
        return

    # 【页面导航配置】定义侧边栏各页面的内部名称和英文显示名称。
    pages = {"dashboard":"📊 Dashboard", "products":"📦 Product Management", "stock":"🔁 Inbound / Outbound", "analytics":"📈 Analytics", "restock":"🧠 Restock", "history":"🧾 Transaction History", "password":"🔐 Change Password"}
    # 【侧边栏与账户操作】显示当前用户、角色、页面导航、刷新按钮和 Logout。
    with st.sidebar:
        st.markdown("## 📦 Inventory System")
        st.caption(f"Signed in as **{st.session_state.user['username']}**")
        st.caption(f"Role: `{st.session_state.user['role'].upper()}`")
        st.divider()
        page = st.radio("Navigation", list(pages.keys()), format_func=lambda x: pages[x], key="page_nav", label_visibility="collapsed")
        st.divider()
        if st.button("🔄 Refresh data", use_container_width=True):
            st.rerun()
        if st.button("🚪 Logout", use_container_width=True, key="logout_button"):
            # page_nav belongs to the st.radio widget above. Do not assign to its
            # session-state key in the same run after the widget is instantiated.
            # The login screen is shown on rerun; the next login resets page_nav.
            st.session_state.user = None
            st.rerun()

    st.title(pages[page])
    # 【页面分流】根据用户在侧边栏选择的页面，调用对应页面函数。
    if page == "dashboard": dashboard_page()
    elif page == "products": product_page()
    elif page == "stock": stock_page()
    elif page == "analytics": analytics_page()
    elif page == "restock": restock_page()
    elif page == "history": history_page()
    elif page == "password": password_page()


if __name__ == "__main__":
    main()
