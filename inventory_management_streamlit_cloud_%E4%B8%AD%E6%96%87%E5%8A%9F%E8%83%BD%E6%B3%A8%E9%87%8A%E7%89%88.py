# ============================================================
# 智能商业库存管理系统（Streamlit 云端版本）
# 本文件的界面文字保持英文；以下中文注释用于说明每个代码区域的功能。
# 系统使用 Supabase 保存用户、产品及库存交易数据。
# ============================================================

from datetime import datetime, timedelta
import hashlib
import pandas as pd
import streamlit as st
from supabase import create_client

# 【系统配置与页面初始化】设置网页标题、图标、布局，以及默认供应商清单。
# Cloud-persistent edition: Supabase PostgreSQL backend

st.set_page_config(page_title="Intelligent Business Inventory Management System", page_icon="📦", layout="wide")

# Example suppliers available in the dropdown menus.
# 【默认供应商】新增或编辑产品时可选择这两家示例供应商。
DEFAULT_SUPPLIERS = ["3M", "Uline"]

@st.cache_resource
# 【数据库连接】读取 Streamlit Secrets 中的 Supabase URL 和 Key，并建立云端数据库连接。
def db_client():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# 【数据库连接快捷函数】提供统一入口，供其他函数调用 Supabase。
def db():
    return db_client()


# 【密码安全处理】将密码转换为 SHA-256 哈希值，数据库不直接保存明文密码。
def pw_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# 【时间处理】生成统一格式的当前日期和时间，供产品及交易记录使用。
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 【用户登录验证】根据用户名查询用户，并比较密码哈希值，成功则返回用户资料。
def authenticate(username, password):
    rows = db().table("users").select("id,username,password_hash,role").eq("username", username.strip()).limit(1).execute().data
    if rows and rows[0]["password_hash"] == pw_hash(password):
        return rows[0]
    return None


# 【产品资料查询】从 Supabase 读取产品，可按产品名称、类别进行搜索和筛选。
def fetch_products(search="", category="All"):
    q = db().table("products").select("*").order("name")
    if search.strip():
        # Supabase/PostgREST OR filter for name/category search
        s = search.strip().replace(",", "")
        q = q.or_(f"name.ilike.%{s}%,category.ilike.%{s}%")
    if category != "All":
        q = q.eq("category", category)
    return q.execute().data or []


# 【类别选项查询】读取数据库中已有的产品类别，并整理成下拉菜单选项。
def fetch_categories():
    rows = db().table("products").select("category").execute().data or []
    return sorted({r["category"] for r in rows})


# 【供应商资料整理】把供应商文字或列表统一转换成去重后的供应商名称列表。
def parse_suppliers(value):
    """Convert a supplier string or multiselect list into a clean list."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")
    return sorted({str(x).strip() for x in items if str(x).strip()}, key=str.casefold)


# 【单项产品查询】根据产品 ID 取得指定产品的完整资料。
def fetch_product(product_id):
    rows = db().table("products").select("*").eq("id", int(product_id)).limit(1).execute().data
    return rows[0] if rows else None


# 【新增产品】保存产品资料、初始库存和供应商；初始库存大于 0 时同时建立入库交易记录。
def add_product(name, category, quantity, min_stock, price, suppliers, username):
    payload = {"name": name.strip(), "category": category.strip(), "quantity": int(quantity),
               "min_stock": int(min_stock), "price": float(price), "suppliers": ", ".join(parse_suppliers(suppliers)), "created_at": now_text()}
    row = db().table("products").insert(payload).execute().data[0]
    if int(quantity) > 0:
        db().table("transactions").insert({
            "product_id": row["id"], "product_name": name.strip(), "kind": "IN",
            "quantity": int(quantity), "price": float(price), "note": "Initial stock",
            "username": username, "created_at": now_text()
        }).execute()


# 【编辑产品】更新产品名称、类别、最低库存、价格及供应商资料。
def update_product(product_id, name, category, min_stock, price, suppliers):
    db().table("products").update({"name": name.strip(), "category": category.strip(),
        "min_stock": int(min_stock), "price": float(price), "suppliers": ", ".join(parse_suppliers(suppliers))}).eq("id", int(product_id)).execute()


# 【删除产品】根据产品 ID 从数据库删除指定产品。
def delete_product(product_id):
    db().table("products").delete().eq("id", int(product_id)).execute()


# 【库存进出记录】调用 Supabase 数据库函数，原子化更新库存并新增一笔交易记录。
def record_stock(product_id, kind, quantity, note, username):
    # Database RPC makes stock update + transaction insert one atomic operation.
    result = db().rpc("record_stock", {
        "p_product_id": int(product_id), "p_kind": kind, "p_quantity": int(quantity),
        "p_note": note.strip(), "p_username": username
    }).execute()
    return result


# 【交易历史查询】按最新记录优先读取库存进出历史。
def fetch_transactions(limit=500):
    return db().table("transactions").select("*").order("id", desc=True).limit(limit).execute().data or []


# 【仪表板统计】计算产品总数、库存总件数、库存总价值及低库存产品数量。
def dashboard_data():
    products = fetch_products()
    return {
        "products": len(products),
        "units": sum(int(p["quantity"]) for p in products),
        "value": sum(int(p["quantity"]) * float(p["price"]) for p in products),
        "low": sum(1 for p in products if int(p["quantity"]) <= int(p["min_stock"]))
    }


# 【补货计算】根据近 30 天出库量、每日需求和最低库存，计算目标库存及建议补货数量。
def restock_data():
    products = fetch_products()
    cutoff = datetime.now() - timedelta(days=30)
    txs = fetch_transactions(5000)
    out = []
    for p in products:
        sold = 0
        for t in txs:
            if t.get("product_id") == p["id"] and t.get("kind") == "OUT":
                try:
                    if datetime.strptime(t["created_at"][:19], "%Y-%m-%d %H:%M:%S") >= cutoff:
                        sold += int(t["quantity"])
                except (ValueError, TypeError):
                    pass
        target = max(int(p["min_stock"]) * 2, round((sold / 30) * 14))
        out.append({"ID": p["id"], "Product Name": p["name"], "Current Stock": p["quantity"],
                    "Min Stock": p["min_stock"], "30-Day Outbound": sold,
                    "Daily Demand": f"{sold / 30:.2f}", "Target Stock": target,
                    "Recommended Reorder Quantity": max(0, target - int(p["quantity"]))})
    return out


# 【分析图表数据】统计近 30 天各类别出库数量，以及出库量最高的产品。
def analytics_data():
    products = fetch_products()
    txs = fetch_transactions(5000)
    cutoff = datetime.now() - timedelta(days=30)
    cat_totals, recent_totals = {}, {}
    for t in txs:
        if t.get("kind") != "OUT":
            continue
        try:
            dt = datetime.strptime(t["created_at"][:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, KeyError):
            continue
        if dt < cutoff:
            continue
        name = t.get("product_name", "Unknown")
        recent_totals[name] = recent_totals.get(name, 0) + int(t["quantity"])
        product = next((p for p in products if p["id"] == t.get("product_id")), None)
        if product:
            cat = product["category"]
            cat_totals[cat] = cat_totals.get(cat, 0) + int(t["quantity"])
    categories = [{"category": k, "total": v} for k, v in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)]
    recent = [{"product_name": k, "total": v} for k, v in sorted(recent_totals.items(), key=lambda x: x[1], reverse=True)[:8]]
    return categories, recent


# 【AI 分析数据摘要】整理产品总数、库存总量、低库存清单、出库量和库存价值，作为 AI 输入。
def build_analytics_summary():
    products = fetch_products()
    txs = fetch_transactions(5000)
    cutoff = datetime.now() - timedelta(days=30)
    outbound = [t for t in txs if t.get("kind") == "OUT" and
                (lambda d: d is not None and d >= cutoff)(parse_tx_datetime(t.get("created_at", "")))]
    low = [p for p in products if int(p["quantity"]) <= int(p["min_stock"])]
    out_by_product = {}
    for t in outbound:
        name = t.get("product_name", "Unknown")
        out_by_product[name] = out_by_product.get(name, 0) + int(t["quantity"])
    top = sorted(out_by_product.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "product_count": len(products),
        "total_units": sum(int(p["quantity"]) for p in products),
        "low_stock": [{"name": p["name"], "stock": int(p["quantity"]), "minimum": int(p["min_stock"])} for p in low],
        "outbound_30d": sum(int(t["quantity"]) for t in outbound),
        "top_outbound": top,
        "stock_value_rm": round(sum(int(p["quantity"]) * float(p["price"]) for p in products), 2),
    }


# 【交易时间解析】把数据库中的时间文字转换为日期时间对象；无法解析时返回空值。
def parse_tx_datetime(value):
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# 【AI 库存分析】优先调用 OpenAI 模型生成英文分析；API 不可用或没有 Key 时改用规则分析。
def generate_ai_analysis(summary):
    """Generate fresh insights on each Analytics page run; use rules if no API key."""
    prompt = (
        "You are an inventory analyst. Analyze the following live inventory snapshot. "
        "Write concise, practical insights in English with these headings: Overview, Stock Risks, Outbound Trends, Recommended Actions. "
        "Only use the supplied data. Do not invent causes, forecasts, or numbers. "
        "If data is insufficient, state that clearly. Data: " + str(summary)
    )
    try:
        api_key = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        api_key = ""
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content, True
        except Exception as e:
            return f"The AI service is temporarily unavailable: {e}\n\n" + fallback_analytics(summary), False
    return fallback_analytics(summary), False


# 【备用规则分析】不调用外部 AI，根据库存数量、低库存和出库数据生成分析及建议。
def fallback_analytics(summary):
    low = summary["low_stock"]
    top = summary["top_outbound"]
    lines = [
        "### Overview\n"
        f"There are **{summary['product_count']}** product types, **{summary['total_units']}** units in stock, and a total stock value of approximately **RM {summary['stock_value_rm']:,.2f}**.",
        "### Stock Risks\n" + ("Products at or below minimum stock: " + ", ".join(f"{x['name']} (current: {x['stock']}, minimum: {x['minimum']})" for x in low) if low else "No products are currently at or below the minimum stock level."),
        "### Outbound Trends\n"
        f"Total outbound quantity over the last 30 days: **{summary['outbound_30d']}** units. " + ("Top outgoing products: " + ", ".join(f"{n} ({q} units)" for n, q in top) + "." if top else "There are no outbound transactions in the last 30 days."),
        "### Recommended Actions\n" + ("Review low-stock products first and arrange purchases based on the restocking recommendations." if low else "Continue monitoring stock levels and review products with higher outbound quantities regularly."),
    ]
    return "\n\n".join(lines)


# 【修改密码】验证旧密码、检查新密码长度，并更新数据库中的密码哈希。
def change_password(username, old_password, new_password):
    if not authenticate(username, old_password):
        return False, "The current password is incorrect."
    if len(new_password) < 8:
        return False, "The new password must be at least 8 characters long."
    db().table("users").update({"password_hash": pw_hash(new_password)}).eq("username", username).execute()
    return True, "Password updated successfully."


# 【登录页面】显示登录表单，验证账号密码，并在成功后进入系统主页面。
def login_screen():
    st.title("📦 Intelligent Business Inventory Management System")
    st.caption("Inventory overview · Product management · Stock movement · Analytics")
    _, center, _ = st.columns([1, 1.15, 1])
    with center:
        with st.container(border=True):
            st.subheader("Sign in")
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
            if submitted:
                user = authenticate(username, password)
                if user:
                    st.session_state.user = user
                    st.session_state.page_nav = "dashboard"
                    st.rerun()
                else:
                    st.error("Invalid username or password.")


# 【Dashboard 仪表板页面】展示库存关键指标，并列出低于或达到最低库存的产品。
def dashboard_page():
    st.subheader("Inventory Overview")
    data = dashboard_data()
    cols = st.columns(4)
    for col, (label, value) in zip(cols, [("Total Products", data["products"]), ("Total Units", data["units"]),
            ("Total Stock Value", f"RM {data['value']:,.2f}"), ("Low Stock Items", data["low"]) ]):
        col.metric(label, value)
    products = fetch_products()
    low = [p for p in products if int(p["quantity"]) <= int(p["min_stock"])]
    st.markdown("### Stock Status")
    if low:
        st.warning(f"{len(low)} product(s) are at or below minimum stock.")
        st.dataframe(pd.DataFrame(low), use_container_width=True, hide_index=True)
    else:
        st.success("All products are above their minimum stock level.")


# 【Product Management 产品管理页面】搜索和查看产品；仅 Admin 可新增、编辑或删除产品，Staff 只读。
def product_page():
    st.subheader("Product Management")
    user = st.session_state.user
    with st.container(border=True):
        c1, c2 = st.columns([2, 1])
        search = c1.text_input("Search by product name or category", key="product_search")
        category = c2.selectbox("Category", ["All"] + fetch_categories(), key="product_category")
    products = fetch_products(search, category)
    if products:
        df = pd.DataFrame(products)
        df["Stock Value (RM)"] = (df["quantity"] * df["price"]).round(2)
        df["Status"] = df.apply(lambda r: "LOW STOCK" if r["quantity"] <= r["min_stock"] else "OK", axis=1)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No products match your search.")

    if user["role"] != "admin":
        st.info("Staff access: You can view product information only. Adding, editing, and deleting products are restricted to Admin users.")
        return

    action = st.radio("Product action", ["Add product", "Edit product", "Delete product"], horizontal=True, key="product_action")
    if action == "Add product":
        with st.form("add_product_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Product name")
            category_new = c2.text_input("Category")
            quantity = c1.number_input("Initial stock", min_value=0, step=1, value=0)
            min_stock = c2.number_input("Minimum stock", min_value=0, step=1, value=5)
            price = c1.number_input("Unit price (RM)", min_value=0.0, step=1.0, format="%.2f")
            suppliers = st.multiselect(
                "Suppliers",
                options=DEFAULT_SUPPLIERS,
                default=DEFAULT_SUPPLIERS,
                help="Select one or both example suppliers for this product."
            )
            submit = st.form_submit_button("Add product", type="primary")
        if submit:
            if not name.strip() or not category_new.strip():
                st.error("Product name and category cannot be empty.")
            else:
                try:
                    add_product(name, category_new, int(quantity), int(min_stock), float(price), suppliers, user["username"])
                    st.success("Product added successfully.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add product: {e}")
    elif action == "Edit product":
        if user["role"] != "admin":
            st.info("Only administrators can edit products.")
        elif not products:
            st.info("There are no products to edit.")
        else:
            options = {f'{p["id"]} | {p["name"]}': p["id"] for p in fetch_products()}
            chosen = st.selectbox("Select product", list(options.keys()), key="edit_product_select")
            p = fetch_product(options[chosen])
            with st.form("edit_product_form"):
                c1, c2 = st.columns(2)
                new_name = c1.text_input("Product name", value=p["name"])
                new_category = c2.text_input("Category", value=p["category"])
                c1.number_input("Current stock (read-only)", min_value=0, value=int(p["quantity"]), disabled=True)
                new_min = c2.number_input("Minimum stock", min_value=0, value=int(p["min_stock"]), step=1)
                new_price = c1.number_input("Unit price (RM)", min_value=0.0, value=float(p["price"]), step=1.0, format="%.2f")
                saved_suppliers = parse_suppliers(p.get("suppliers"))
                supplier_defaults = [name for name in saved_suppliers if name in DEFAULT_SUPPLIERS]
                new_suppliers = st.multiselect(
                    "Suppliers",
                    options=DEFAULT_SUPPLIERS,
                    default=supplier_defaults,
                    help="Select the suppliers associated with this product."
                )
                save = st.form_submit_button("Save changes", type="primary")
            if save:
                update_product(p["id"], new_name, new_category, int(new_min), float(new_price), new_suppliers)
                st.success("Product updated.")
                st.rerun()
    else:
        if user["role"] != "admin":
            st.info("Only administrators can delete products.")
        elif not products:
            st.info("There are no products to delete.")
        else:
            opts = {f'{p["id"]} | {p["name"]} (stock: {p["quantity"]})': p["id"] for p in fetch_products()}
            selected = st.selectbox("Select product", list(opts.keys()), key="delete_product_select")
            confirm = st.checkbox("I understand this product will be deleted.", key="confirm_delete_product")
            if st.button("Delete selected product", type="primary", disabled=not confirm):
                delete_product(opts[selected])
                st.success("Product deleted.")
                st.rerun()


# 【Inbound / Outbound 入库出库页面】选择产品、供应商、进出库类型和数量，并提交库存交易。
def stock_page():
    st.subheader("Inbound / Outbound Stock")
    products = fetch_products()
    if not products:
        st.info("Add a product before recording stock movement.")
        return
    opts = {f'{p["id"]} | {p["name"]} (stock: {p["quantity"]})': p for p in products}
    # Keep product and supplier selectors outside the form so supplier options
    # update immediately when a different product is selected.
    selected = st.selectbox("Product", list(opts.keys()), key="stock_product_select")
    supplier_options = parse_suppliers(opts[selected].get("suppliers")) or DEFAULT_SUPPLIERS
    supplier = st.selectbox("Supplier", supplier_options, key="stock_supplier_select")
    with st.form("stock_movement_form", clear_on_submit=True):
        movement = st.radio("Movement type", ["Incoming (IN)", "Outgoing (OUT)"], horizontal=True)
        qty = st.number_input("Quantity", min_value=1, step=1, value=1)
        submit = st.form_submit_button("Submit stock change", type="primary")
    if submit:
        kind = "IN" if movement.startswith("Incoming") else "OUT"
        try:
            record_stock(opts[selected]["id"], kind, int(qty), supplier, st.session_state.user["username"])
            st.success("Stock movement recorded.")
            st.rerun()
        except Exception as e:
            st.error(str(e))


# 【Analytics 数据分析页面】展示库存指标、类别出库图表、热销出库产品及 AI/规则分析结果。
def analytics_page():
    st.subheader("Inventory Analytics")
    st.caption("AI analysis is regenerated using the latest inventory data and outbound transactions from the last 30 days whenever this page is opened or refreshed.")
    if st.button("🔄 Refresh Latest Analysis", type="primary", key="refresh_ai_analytics"):
        st.rerun()
    summary = build_analytics_summary()
    m1, m2, m3 = st.columns(3)
    m1.metric("Products", summary["product_count"])
    m2.metric("30-Day Outbound", summary["outbound_30d"])
    m3.metric("Low Stock Items", len(summary["low_stock"]))
    categories, recent = analytics_data()
    if categories:
        df = pd.DataFrame(categories).rename(columns={"category": "Category", "total": "Outbound quantity (30 days)"})
        left, right = st.columns(2)
        left.bar_chart(df.set_index("Category"))
        right.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("There is no outbound analysis data for the last 30 days.")
    st.markdown("#### Most frequently outgoing products (last 30 days)")
    if recent:
        st.dataframe(pd.DataFrame(recent).rename(columns={"product_name": "Product", "total": "Outbound quantity"}), use_container_width=True, hide_index=True)
    else:
        st.info("No outgoing transactions in the last 30 days.")
    st.divider()
    st.markdown("### 🤖 AI Inventory Analysis")
    analysis, used_ai = generate_ai_analysis(summary)
    st.caption("OpenAI AI analysis" if used_ai else "Rule-based analysis mode (add OPENAI_API_KEY to Streamlit Secrets to enable AI analysis).")
    st.markdown(analysis)


# 【Restocking Recommendations 补货建议页面】展示每项产品的目标库存和建议补货数量。
def restock_page():
    st.subheader("Restocking Recommendations")
    st.info("Target Stock = max(Minimum Stock × 2, Average Daily Outbound × 14 days).")
    rows = restock_data()
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        need = df[df["Recommended Reorder Quantity"] > 0]
        if not need.empty:
            st.warning(f"{len(need)} product(s) have a positive reorder recommendation.")


# 【Transaction History 交易历史页面】查看、搜索及筛选入库和出库记录。
def history_page():
    st.subheader("Transaction History")
    txs = fetch_transactions()
    if not txs:
        st.info("No transactions recorded yet.")
        return
    df = pd.DataFrame(txs)
    df["kind"] = df["kind"].map({"IN": "Incoming", "OUT": "Outgoing"})
    df = df.rename(columns={"id": "ID", "created_at": "Date / Time", "product_name": "Product", "kind": "Type", "quantity": "Quantity", "price": "Unit Price (RM)", "note": "Supplier", "username": "User"})
    c1, c2 = st.columns([2, 1])
    search = c1.text_input("Filter by product or user", key="history_search").strip().lower()
    kind = c2.selectbox("Movement type", ["All", "Incoming", "Outgoing"], key="history_type")
    if search:
        df = df[df["Product"].astype(str).str.lower().str.contains(search, na=False) | df["User"].astype(str).str.lower().str.contains(search, na=False)]
    if kind != "All":
        df = df[df["Type"] == kind]
    st.dataframe(df, use_container_width=True, hide_index=True)


# 【Change Password 修改密码页面】提供旧密码、新密码及确认密码表单。
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
    pages = {"dashboard":"📊 Dashboard", "products":"📦 Product Management", "stock":"🔁 Inbound / Outbound", "analytics":"📈 Analytics", "restock":"🧠 Restocking Recommendations", "history":"🧾 Transaction History", "password":"🔐 Change Password"}
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
