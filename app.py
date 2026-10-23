import streamlit as st
import requests, json, time, os

# 1. 设置页面全局样式为宽屏（最大化可视面积）
st.set_page_config(
    page_title="Auto Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 常规配置
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "your_username/auto-video-maker")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # 在 Streamlit Secrets 中配置
SHEET_EDIT_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"

# 嵌入视窗网址 (Google Sheets iframe html 格式)
SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/htmlview"

# 极简 UI 隐藏默认多余元素
st.markdown("""
    <style>
        .block-container {padding-top: 1rem; padding-bottom: 0rem; padding-left: 2rem; padding-right: 2rem;}
        iframe {border-radius: 8px; border: 1px solid #ddd;}
    </style>
""", unsafe_allow_html=True)

# Helper: 请求 GitHub API 触发渲染
def trigger_github_workflow(tab_name):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}" if GITHUB_TOKEN else ""
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/render.yml/dispatches"
    payload = {"ref": "main", "inputs": {"sheet_tab": tab_name}}
    res = requests.post(url, headers=headers, json=payload)
    return res.status_code == 204

# Helper: 自动查询最新运行的任务（支持离线恢复查看结果）
def get_latest_workflow_run():
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}" if GITHUB_TOKEN else ""
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs?per_page=1"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        if data.get("workflow_runs"):
            return data["workflow_runs"][0]
    return None

# ==============================================================================
# 📌 侧边控制面板（设置、渲染触发、后台状态与视频保留播放器）
# ==============================================================================
with st.sidebar:
    st.title("🎬 Studio Control")
    st.markdown("---")
    
    # 填写的标签页名称
    target_tab = st.text_input("Target Sheet Tab", value="script", help="输入 Google Sheet 底部的 Tab 名称")
    
    # 渲染控制按钮
    if st.button("🚀 Start Rendering", type="primary", use_container_width=True):
        if trigger_github_workflow(target_tab):
            st.success("✅ 云端渲染任务已启动！你可以随时关闭浏览器，后台会自动运行。")
            time.sleep(1)
            st.rerun()
        else:
            st.error("❌ 启动失败，请检查 GITHUB_TOKEN 配置。")

    st.markdown("---")
    st.subheader("📡 Live Action Status")
    
    # 自动异步检查 GitHub Actions 的最新状态（关闭网页重载依然有效！）
    run_info = get_latest_workflow_run()
    if run_info:
        status = run_info.get("status")
        conclusion = run_info.get("conclusion")
        html_url = run_info.get("html_url")
        
        if status in ["in_progress", "queued"]:
            st.info(f"⏳ **云端渲染中...** ({status})")
            st.progress(50)
            st.caption("您可以关闭此页面，渲染完成后重新打开即可查看视频。")
            if st.button("🔄 刷新进度"):
                st.rerun()
        elif status == "completed":
            if conclusion == "success":
                st.success("🎉 **视频渲染成功！**")
                st.markdown(f"[🔗 查看 GitHub 任务详情]({html_url})")
                st.markdown("### 📥 渲染成品下载")
                st.info("提示：成品可通过 GitHub Artifacts 界面或网页右侧查看下载。")
            else:
                st.error(f"❌ 渲染失败，结论: {conclusion}")
                st.markdown(f"[🔍 点击查看错误日志]({html_url})")
    else:
        st.caption("暂无云端运行记录。")

# ==============================================================================
# 📊 主界面：表格可视视窗最大化 + 顶部工具条
# ==============================================================================
col_title, col_btn = st.columns([3, 1])
with col_title:
    st.subheader("📋 Google Sheets 内容在线编辑")
with col_btn:
    st.markdown(f'''
        <a href="{SHEET_EDIT_URL}" target="_blank">
            <button style="
                width: 100%;
                background-color: #4CAF50;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;">
                🔗 新标签页打开表格
            </button>
        </a>
    ''', unsafe_allow_html=True)

# 内嵌嵌入视窗直接放大占满屏幕
st.components.v1.iframe(SHEET_EMBED_URL, height=730, scrolling=True)
