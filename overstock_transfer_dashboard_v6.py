
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="خطة تحويلات الأوفر استوك بين الصيدليات (فلاتر متقدمة)", layout="wide")

@st.cache_data
def load_data(uploaded_file=None):
    """
    تحميل بيانات المبيعات والأوفر ستوك.
    لو مفيش فايل مرفوع بيحاول يقرأ من فايل افتراضي في نفس المجلد.
    """
    if uploaded_file is not None:
        return pd.read_excel(uploaded_file)
    default_name = "Final Sales from 01-09 To 12-11-2025 all stores.xlsx"
    try:
        return pd.read_excel(default_name)
    except Exception:
        st.error("⚠️ من فضلك ارفع ملف الإكسيل من الشمال، أو حط الملف جنب الكود بنفس الاسم.")
        st.stop()

def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    تنظيف أسماء الأعمدة:
    - إزالة المسافات من البداية والنهاية
    - تحويل لحروف صغيرة
    - توحيد المسافات داخل الاسم
    """
    cols = (
        df.columns
        .astype(str)
        .str.replace('\u00A0', ' ', regex=False)   # مسافات غير قياسية
        .str.strip()
        .str.lower()
        .str.replace(r'\s+', ' ', regex=True)
    )
    df.columns = cols
    return df

def build_allocation_plan(
    df,
    min_keep=1,
    data_days=70,
    coverage_days=45,
    require_sale_for_need=True
):
    """
    بناء خطة تحويلات على أساس:
    - متوسط المبيعات اليومي = إجمالي مبيعات الـ data_days / data_days
    - المخزون المستهدف = متوسط اليومي * coverage_days
    - الأوفر استوك = المخزون الحالي - المخزون المستهدف
    - الصيدليات المانحة: عندها أوفر > 0 بعد ترك min_keep
    - الصيدليات المحتاجة: مخزونها أقل من المستهدف (need > 0)
    ثم:
    - دمج التحويلات لنفس (الصنف، من فرع، إلى فرع) في صف واحد.
    """
    required_cols = [
        "wh_cd", "itm_cd", "itm_name", "subcatname",
        "qtyonhand", "saleqty"
    ]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"⚠️ العمود '{col}' (بعد التنظيف) مش موجود في الشيت.")
            st.stop()

    df = df.copy()

    # تأمين عدم القسمة على صفر
    if data_days <= 0:
        data_days = 1

    # متوسط المبيعات اليومي لكل صنف في كل صيدلية
    df["avg_daily_sale"] = df["saleqty"] / float(data_days)

    # المخزون المستهدف (مقرب لأعلى)
    df["target_stock"] = np.ceil(df["avg_daily_sale"] * coverage_days).astype(int)

    # الأوفر استوك المحسوب
    df["over_stock_calc"] = df["qtyonhand"] - df["target_stock"]

    allocations = []

    # نجمع حسب كود الصنف
    for item_code, group in df.groupby("itm_cd"):
        # الصيدليات المانحة
        donors = group.copy()
        donors = donors[(donors["over_stock_calc"] > 0) & (donors["qtyonhand"] > min_keep)]

        if donors.empty:
            continue

        # الصيدليات المحتاجة: مخزونها أقل من المستهدف
        receivers = group.copy()
        receivers["need"] = (receivers["target_stock"] - receivers["qtyonhand"]).astype(int)
        receivers = receivers[receivers["need"] > 0]

        if require_sale_for_need:
            receivers = receivers[receivers["saleqty"] > 0]

        if receivers.empty:
            continue

        # حساب المتاح للتحويل من كل مانح
        donors["available_to_transfer"] = np.minimum(
            donors["over_stock_calc"],
            donors["qtyonhand"] - min_keep
        ).astype(int)
        donors = donors[donors["available_to_transfer"] > 0]

        if donors.empty:
            continue

        # ترتيب المانحين من أعلى أوفر استوك
        donors = donors.sort_values("over_stock_calc", ascending=False)

        # ترتيب المستقبلين حسب أعلى احتياج ثم أعلى مبيعات
        receivers = receivers.sort_values(["need", "saleqty"], ascending=[False, False])

        for r_idx, r in receivers.iterrows():
            remaining_need = int(r["need"])
            if remaining_need <= 0:
                continue

            for d_idx, d in donors.iterrows():
                available = int(donors.at[d_idx, "available_to_transfer"])
                if available <= 0:
                    continue
                if remaining_need <= 0:
                    break

                transfer_qty = min(remaining_need, available)
                if transfer_qty <= 0:
                    continue

                allocations.append({
                    "Itm_Cd": int(item_code),
                    "Itm_Name": r["itm_name"],
                    "SUBCATNAME": r["subcatname"],
                    "From_Wh_Cd": d["wh_cd"],
                    "To_Wh_Cd": r["wh_cd"],
                    "Transfer_Qty": int(transfer_qty),
                    # معلومات إضافية للمراجعة فقط
                    "Donor_QTYONHAND_before": int(d["qtyonhand"]),
                    "Receiver_QTYONHAND_before": int(r["qtyonhand"]),
                    "Donor_over_stock_calc": int(d["over_stock_calc"]),
                    "Receiver_need": int(r["need"]),
                    "Avg_Daily_Sale": float(r["avg_daily_sale"]),
                    "Target_Stock": int(r["target_stock"]),
                    "Data_Days": int(data_days),
                    "Coverage_Days": int(coverage_days)
                })

                # نحدث المتبقي
                remaining_need -= transfer_qty
                donors.at[d_idx, "available_to_transfer"] = available - transfer_qty

    if not allocations:
        return pd.DataFrame()

    alloc_df = pd.DataFrame(allocations)

    # 🧮 دمج التحويلات لنفس (الصنف، من فرع، إلى فرع) في صف واحد
    group_cols = [
        "Itm_Cd", "Itm_Name", "SUBCATNAME",
        "From_Wh_Cd", "To_Wh_Cd",
        "Data_Days", "Coverage_Days"
    ]

    agg_dict = {
        "Transfer_Qty": "sum",
        "Donor_QTYONHAND_before": "first",
        "Receiver_QTYONHAND_before": "first",
        "Donor_over_stock_calc": "first",
        "Receiver_need": "first",
        "Avg_Daily_Sale": "first",
        "Target_Stock": "first",
    }

    alloc_df = (
        alloc_df
        .groupby(group_cols, as_index=False)
        .agg(agg_dict)
    )

    alloc_df["Item_Key"] = alloc_df["Itm_Cd"].astype(str) + " - " + alloc_df["Itm_Name"].astype(str)
    return alloc_df

def to_excel(alloc_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        alloc_df.to_excel(writer, index=False, sheet_name="Transfer Plan")
        original_df.to_excel(writer, index=False, sheet_name="Source Data")
    output.seek(0)
    return output

def main():
    st.title("📦 خطة تحويلات الأوفر استوك بين الصيدليات (فلاتر متقدمة)")
    st.caption("دمج التحويلات + فلاتر منفصلة للصيدليات المانحة والمستلمة + استبعاد جروبات من فروع معينة (مانحة أو مستلمة).")

    st.sidebar.header("⚙️ الإعدادات الأساسية")

    uploaded_file = st.sidebar.file_uploader(
        "ارفع ملف المبيعات والاستوك (Excel)",
        type=["xlsx", "xls"]
    )

    data_days = st.sidebar.number_input(
        "عدد الأيام التي تغطيها مبيعات الملف",
        min_value=1,
        value=70,
        step=1,
        help="مثلاً 70 يوم كما ذكرت."
    )

    coverage_days = st.sidebar.number_input(
        "عدد أيام الاحتياج المستهدفة في كل فرع",
        min_value=1,
        value=45,
        step=1,
        help="المخزون المطلوب = متوسط المبيعات اليومي × عدد الأيام دي."
    )

    min_keep = st.sidebar.number_input(
        "أقل كمية نسيبها في كل صيدلية مانحة",
        min_value=0,
        value=1,
        step=1
    )

    require_sale_for_need = st.sidebar.checkbox(
        "اشترط إن الصيدلية تكون باعِت الصنف قبل ما نعتبرها محتاجة؟",
        value=True
    )

    df_raw = load_data(uploaded_file)
    df = normalize_dataframe(df_raw.copy())

    st.subheader("📊 معاينة البيانات بعد التنظيف")
    with st.expander("عرض أول 100 صف من البيانات"):
        st.dataframe(df.head(100), use_container_width=True)

    st.sidebar.markdown("---")
    st.sidebar.subheader("🔎 فلاتر العرض بعد الحساب")

    base_cols = ["wh_cd", "subcatname", "itm_name"]
    if all(col in df.columns for col in base_cols):
        all_branches = sorted(df["wh_cd"].unique().tolist())
        all_groups = sorted(df["subcatname"].unique().tolist())
        all_items = sorted(df["itm_name"].unique().tolist())
    else:
        all_branches, all_groups, all_items = [], [], []

    # فلاتر منفصلة للمانحين والمستلمين
    donor_filter = st.sidebar.multiselect(
        "فلتر الصيدليات المانحة (From_Wh_Cd)",
        all_branches
    )

    receiver_filter = st.sidebar.multiselect(
        "فلتر الصيدليات المستلمة (To_Wh_Cd)",
        all_branches
    )

    group_filter = st.sidebar.multiselect(
        "فلتر عام للجروبات (SUBCATNAME)",
        all_groups
    )

    item_filter = st.sidebar.multiselect(
        "فلتر الأصناف بالاسم",
        all_items
    )

    st.sidebar.markdown("----")
    st.sidebar.subheader("🚫 استبعاد جروبات من صيدليات مستلمة معينة")

    branch_for_group_exclude = st.sidebar.selectbox(
        "اختر الصيدلية التي تريد استبعاد جروب منها (كمستلمة)",
        ["لا يوجد"] + all_branches
    )

    if branch_for_group_exclude != "لا يوجد":
        excluded_groups_receiver = st.sidebar.multiselect(
            "اختر الجروبات التي تريد استبعادها من هذه الصيدلية (كمستلمة)",
            all_groups,
            key="exclude_groups_for_branch_receiver"
        )
    else:
        excluded_groups_receiver = []

    st.sidebar.markdown("----")
    st.sidebar.subheader("🚫 استبعاد جروبات من صيدليات مانحة معينة (متعدد)")

    excluded_donor_branches = st.sidebar.multiselect(
        "اختر الصيدليات المانحة التي تريد استبعاد جروبات منها",
        all_branches,
        key="excluded_donor_branches"
    )

    excluded_groups_donor = st.sidebar.multiselect(
        "اختر الجروبات التي تريد استبعادها من هذه الصيدليات المانحة",
        all_groups,
        key="excluded_groups_donor"
    )

    st.markdown("### ▶️ تنفيذ خطة التحويلات")
    run_button = st.button("احسب خطة التحويلات الآن")

    if not run_button:
        st.info("اضغط على زر **احسب خطة التحويلات الآن** لعرض النتائج.")
        return

    with st.spinner("جاري حساب خطة التحويلات ودمج التحويلات المتكررة..."):
        alloc_df = build_allocation_plan(
            df,
            min_keep=min_keep,
            data_days=data_days,
            coverage_days=coverage_days,
            require_sale_for_need=require_sale_for_need
        )

    if alloc_df.empty:
        st.warning("لا توجد تحويلات مقترحة حسب الشروط الحالية.")
        return

    # تطبيق الفلاتر
    filtered = alloc_df.copy()

    if donor_filter:
        filtered = filtered[filtered["From_Wh_Cd"].isin(donor_filter)]

    if receiver_filter:
        filtered = filtered[filtered["To_Wh_Cd"].isin(receiver_filter)]

    if group_filter:
        filtered = filtered[filtered["SUBCATNAME"].isin(group_filter)]

    if item_filter:
        filtered = filtered[filtered["Itm_Name"].isin(item_filter)]

    # استبعاد جروبات من صيدلية معينة (كمستلمة)
    if branch_for_group_exclude != "لا يوجد" and excluded_groups_receiver:
        mask_exclude_recv = (
            (filtered["To_Wh_Cd"] == branch_for_group_exclude) &
            (filtered["SUBCATNAME"].isin(excluded_groups_receiver))
        )
        filtered = filtered[~mask_exclude_recv]

    # استبعاد جروبات من صيدليات مانحة معينة (متعدد)
    if excluded_donor_branches and excluded_groups_donor:
        mask_exclude_donor = (
            filtered["From_Wh_Cd"].isin(excluded_donor_branches) &
            filtered["SUBCATNAME"].isin(excluded_groups_donor)
        )
        filtered = filtered[~mask_exclude_donor]

    st.success(f"تم حساب الخطة بعد الدمج وتطبيق الفلاتر. إجمالي عدد أسطر التحويلات: {len(filtered):,}")

    total_transfer_qty = filtered["Transfer_Qty"].sum()
    num_items = filtered["Itm_Cd"].nunique()
    num_from_branches = filtered["From_Wh_Cd"].nunique()
    num_to_branches = filtered["To_Wh_Cd"].nunique()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("إجمالي كميات التحويل", f"{total_transfer_qty:,}")
    col2.metric("عدد الأصناف في الخطة", f"{num_items:,}")
    col3.metric("عدد الصيدليات المانحة", f"{num_from_branches:,}")
    col4.metric("عدد الصيدليات المستلمة", f"{num_to_branches:,}")

    st.markdown("### 📋 جدول خطة التحويلات المقترحة بعد الدمج والفلاتر")
    st.dataframe(filtered, use_container_width=True)

    st.markdown("### ⬇️ تحميل الخطة في ملف Excel")
    excel_bytes = to_excel(filtered, df_raw)
    st.download_button(
        label="تحميل ملف خطة التحويلات (Excel)",
        data=excel_bytes,
        file_name="Transfer_Plan_Merged_Overstock_AdvancedFilters_v6.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == "__main__":
    main()
