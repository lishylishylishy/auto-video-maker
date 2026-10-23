import streamlit as st
import requests, json, time, os

# 1. 设置页面全局样式为宽屏（最大化可视面积）
st.set_page_config(
    page_title="Auto Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🌟 安全读取配置（优先从 st.secrets 读取，再从环境变量读取，兼容多种命名规则）
def get_secret(key, default=""):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)

GITHUB_TOKEN = get_secret("GITHUB_TOKEN", "")
GITHUB_REPO  = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
GITHUB_REF   = get_secret("GITHUB_REF", "main")  # 支持自定义分支名 (main / master)

SHEET_EDIT_URL  = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"
SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/htmlview"

# 🌟 CSS 边距调优 + 隐藏 Streamlit 顶部原生遮挡栏
st.markdown("""
    <style>
        /* 隐藏顶部原生 Header 栏（Share、Star、Status） */
        header[data-testid="stHeader"] {
            display: none !important;
        }
        /* 页面顶端保留舒适安全间距 */
        .block-container {
            padding-top: 1.2rem !important;
            padding-bottom: 1rem !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }
        iframe {
            border-radius: 8px;
            border: 1px solid #e0e0e0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
    </style>
""", unsafe_allow_html=True)

# Helper: 请求 GitHub API 触发渲染（返回精准错误信息）
def trigger_github_workflow(tab_name):
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    ref = get_secret("GITHUB_REF", "main")

    if not token:
        return False, "未检测到 GITHUB_TOKEN，请在 Streamlit Secrets 中配置。"
    if not repo or repo == "your_username/auto-video-maker":
        return False, f"仓库名称未正确配置 (当前: '{repo}')，请在 Streamlit Secrets 中配置 GITHUB_REPOSITORY (例如: 用户名/仓库名)。"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/render.yml/dispatches"
    payload = {"ref": ref, "inputs": {"sheet_tab": tab_name}}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 204:
            return True, "成功"
        else:
            return False, f"GitHub 返回 [{res.status_code}]: {res.text}"
    except Exception as e:
        return False, f"网络请求异常: {str(e)}"

# Helper: 自动查询最新运行的任务（支持离线恢复查看结果）
def get_latest_workflow_run():
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    
    if not token or not repo or repo == "your_username/auto-video-maker":
        return None
        
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=1"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("workflow_runs"):
                return data["workflow_runs"][0]
    except Exception:
        pass
    return None

# ==============================================================================
# 📌 侧边控制面板（设置、渲染触发、后台状态追踪）
# ==============================================================================
with st.sidebar:
    st.title("🎬 Studio Control")
    st.markdown("---")
    
    target_tab = st.text_input("Target Sheet Tab", value="script", help="输入 Google Sheet 底部的 Tab 名称")
    
    if st.button("🚀 Start Rendering", type="primary", use_container_width=True):
        ok, msg = trigger_github_workflow(target_tab)
        if ok:
            st.success("✅ 云端渲染任务已启动！您可以随时关闭浏览器，后台会自动运行。")
            time.sleep(1)
            st.rerun()
        else:
            st.error(f"❌ 启动失败：{msg}")

    st.markdown("---")
    st.subheader("📡 Live Action Status")
    
    run_info = get_latest_workflow_run()
    if run_info:
        status = run_info.get("status")
        conclusion = run_info.get("conclusion")
        html_url = run_info.get("html_url")
        
        if status in ["in_progress", "queued"]:
            st.info(f"⏳ **云端渲染中...** ({status})")
            st.progress(50)
            st.caption("您可以关闭此页面，渲染完成后重新打开即可查看视频。")
            if st.button("🔄 刷新进度", use_container_width=True):
                st.rerun()
        elif status == "completed":
            if conclusion == "success":
                st.success("🎉 **视频渲染成功！**")
                st.markdown(f"[🔗 查看 GitHub 任务与下载成品]({html_url})")
            else:
                st.error(f"❌ 渲染失败，结论: {conclusion}")
                st.markdown(f"[🔍 点击查看错误日志]({html_url})")
    else:
        st.caption("暂无云端运行记录。")

# ==============================================================================
# 📊 主界面：最大化表格编辑视窗
# ==============================================================================
col_title, col_btn = st.columns([3, 1], vertical_alignment="center")

with col_title:
    st.subheader("📋 Google Sheets 内容在线编辑")

with col_btn:
    st.link_button("🔗 在新标签页打开表格", SHEET_EDIT_URL, use_container_width=True)

st.divider()

# 高清内嵌表格视窗（720px 高度填满屏幕）
st.components.v1.iframe(SHEET_EMBED_URL, height=720, scrolling=True)
