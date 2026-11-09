# -*- coding: utf-8 -*-
"""
MOMO 購物網價格查詢與視覺化 (Streamlit)
執行方式：streamlit run app.py
"""
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from curl_cffi import requests

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HOME_URL = "https://www.momoshop.com.tw/"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ----------------------------------------------------------------------
# 爬蟲 (來自 CODE01)
# ----------------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


def clean_price(value) -> int | None:
    """把 '$1,299' 之類的字串轉成整數，失敗回傳 None。"""
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


@st.cache_data(ttl=600, show_spinner=False)
def crawl_momo(keyword: str, pages: int, delay: float = 1.0):
    """爬取指定關鍵字的前 N 頁，回傳 (DataFrame, 錯誤訊息清單)。"""
    rows, errors = [], []
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    try:
        session.get(HOME_URL, headers=HEADERS, timeout=15)  # 先取得 cookie
    except Exception as e:  # noqa: BLE001
        errors.append(f"取得首頁 cookie 失敗：{e}")

    for page in range(1, pages + 1):
        try:
            resp = session.post(
                API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=20
            )
        except Exception as e:  # noqa: BLE001
            errors.append(f"第 {page} 頁連線失敗：{e}")
            break

        if resp.status_code != 200:
            errors.append(f"第 {page} 頁回應 {resp.status_code}：{resp.text[:200]}")
            break

        try:
            goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        except ValueError:
            errors.append(f"第 {page} 頁回傳內容不是 JSON")
            break

        if not goods:  # 已經沒有更多商品
            break

        for g in goods:
            code = g.get("goodsCode", "")
            rows.append(
                {
                    "頁數": page,
                    "品名": g.get("goodsName", ""),
                    "價格": clean_price(g.get("goodsPrice", "")),
                    "連結": f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={code}"
                    if code
                    else "",
                }
            )
        time.sleep(delay)  # 禮貌性延遲，避免被封鎖

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.dropna(subset=["價格"]).drop_duplicates(subset=["品名", "價格"])
        df["價格"] = df["價格"].astype(int)
        df = df.reset_index(drop=True)
    return df, errors


# ----------------------------------------------------------------------
# 圖表 (來自 CODE02，改用 Plotly，瀏覽器直接顯示中文，不需另外安裝字型)
# ----------------------------------------------------------------------
def add_price_range(df: pd.DataFrame, bins: int = 6) -> pd.DataFrame:
    """依價格分位數切成價格區間，供圓餅圖/旭日圖使用。"""
    df = df.copy()
    try:
        cats = pd.qcut(df["價格"], q=min(bins, df["價格"].nunique()), duplicates="drop")
    except ValueError:
        cats = pd.cut(df["價格"], bins=1)
    df["價格區間"] = cats.astype(str).str.replace(r"[\(\[\]\)]", "", regex=True)
    df["價格區間"] = df["價格區間"].str.replace(",", " ~ ")
    return df


def chart_line(df: pd.DataFrame, keyword: str):
    mean_price = df["價格"].mean()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["價格"],
            mode="lines+markers",
            name="價格",
            text=df["品名"],
            hovertemplate="%{text}<br>價格：$%{y:,}<extra></extra>",
        )
    )
    fig.add_hline(
        y=mean_price,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均 {mean_price:,.0f}",
    )
    fig.update_layout(
        title=f"MOMO「{keyword}」售價折線圖",
        xaxis_title="商品序號",
        yaxis_title="價格 (NTD)",
        height=550,
    )
    return fig


def chart_pie(df: pd.DataFrame, keyword: str):
    d = add_price_range(df)
    counts = d["價格區間"].value_counts().reset_index()
    counts.columns = ["價格區間", "商品數"]
    fig = px.pie(counts, names="價格區間", values="商品數", hole=0.3,
                 title=f"MOMO「{keyword}」價格區間占比")
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(height=550)
    return fig


def chart_sunburst(df: pd.DataFrame, keyword: str):
    d = add_price_range(df)
    # 品名太長會擠在一起，截斷並避免重複
    d["商品"] = d["品名"].str.slice(0, 14) + "…" + d.index.astype(str)
    fig = px.sunburst(
        d,
        path=["價格區間", "商品"],
        values="價格",
        color="價格",
        color_continuous_scale="Blues",
        title=f"MOMO「{keyword}」價格區間旭日圖",
    )
    fig.update_layout(height=650)
    return fig


# ----------------------------------------------------------------------
# Streamlit 介面
# ----------------------------------------------------------------------
st.set_page_config(page_title="MOMO 價格查詢", page_icon="🛒", layout="wide")
st.title("🛒 MOMO 商品價格查詢與視覺化")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    chart_type = st.radio("圖表類型", ["折線圖", "圓餅圖", "旭日圖"])
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
        st.stop()
    with st.spinner(f"正在爬取「{keyword}」前 {pages} 頁…"):
        df, errors = crawl_momo(keyword.strip(), int(pages))
    st.session_state["result"] = (keyword.strip(), df, errors)

if "result" not in st.session_state:
    st.info("請在左側輸入商品與頁數，然後按「開始查詢」。")
    st.stop()

kw, df, errors = st.session_state["result"]

for msg in errors:
    st.warning(msg)

if df.empty:
    st.error("沒有取得任何商品資料，可能被網站擋下或關鍵字無結果。")
    st.stop()

# 價格範圍篩選
lo, hi = int(df["價格"].min()), int(df["價格"].max())
if lo < hi:
    price_range = st.sidebar.slider("價格範圍篩選", lo, hi, (lo, hi))
    df = df[df["價格"].between(*price_range)].reset_index(drop=True)
    if df.empty:
        st.warning("此價格範圍內沒有商品")
        st.stop()

# 統計
c1, c2, c3, c4 = st.columns(4)
c1.metric("商品數", f"{len(df)}")
c2.metric("平均價格", f"${df['價格'].mean():,.0f}")
c3.metric("最高價格", f"${df['價格'].max():,}")
c4.metric("最低價格", f"${df['價格'].min():,}")

# 圖表
if chart_type == "折線圖":
    st.plotly_chart(chart_line(df, kw), use_container_width=True)
elif chart_type == "圓餅圖":
    st.plotly_chart(chart_pie(df, kw), use_container_width=True)
else:
    st.plotly_chart(chart_sunburst(df, kw), use_container_width=True)

# 資料表與下載
st.subheader("商品明細")
st.dataframe(
    df,
    use_container_width=True,
    column_config={"連結": st.column_config.LinkColumn("連結", display_text="前往商品")},
)
st.download_button(
    "⬇️ 下載 CSV",
    data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
    file_name=f"MOMO_{kw}_{datetime.now():%Y%m%d}.csv",
    mime="text/csv",
)
