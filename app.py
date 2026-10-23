import streamlit as st
import requests, json, time, os, zipfile, io

# 1. 设置页面全局样式为宽屏（最大化可视面积）
st.set_page_config(
    page_title="Auto Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🌟 安全读取配置（优先从 st.secrets 读取，兼容环境变量）
def get_secret(key, default=""):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)

GITHUB_TOKEN = get_secret("GITHUB_TOKEN", "")
GITHUB_REPO  = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
GITHUB_REF   = get_secret("GITHUB_REF", "main")

SHEET_EDIT_URL  = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"
SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/htmlview"

# 🌟 彻底隐藏 Streamlit 顶部原生工具栏，防止文字遮挡
st.markdown("""
    <style>
        header[data-testid="stHeader"] {
            display: none !important;
        }
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

# Helper: 请求 GitHub API 触发渲染
def trigger_github_workflow(tab_name):
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    ref = get_secret("GITHUB_REF", "main")

    if not token or not repo or repo == "your_username/auto-video-maker":
        return False, "请检查 Streamlit Secrets 中的 GITHUB_TOKEN 与 GITHUB_REPOSITORY。"

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

# Helper: 查询最新运行的任务
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

# 🌟 核心功能：直接从 GitHub API 拉取渲染生成的 MP4 文件流并在网页端预览
@st.cache_data(ttl=600, show_spinner=False)
def fetch_rendered_video_bytes(run_id):
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    
    if not token or not repo:
        return None
        
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    
    # 1. 获取 Artifacts 列表
    artifacts_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    try:
        res = requests.get(artifacts_url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
            
        artifacts = res.json().get("artifacts", [])
        target_art = None
        for art in artifacts:
            if art.get("name") == "Rendered-Video-Output":
                target_art = art
                break
                
        if not target_art:
            return None
            
        # 2. 下载 ZIP 压缩包
        download_url = target_art.get("archive_download_url")
        zip_res = requests.get(download_url, headers=headers, timeout=30)
        if zip_res.status_code != 200:
            return None
            
        # 3. 解压提取 .mp4 视频文件
        with zipfile.ZipFile(io.BytesIO(zip_res.content)) as z:
            for filename in z.namelist():
                if filename.endswith(".mp4"):
                    return z.read(filename)
    except Exception as e:
        print(f"提取视频失败: {e}")
        
    return None

# ==============================================================================
# 📌 侧边控制面板（渲染控制、实时状态与内嵌视频播放器）
# ==============================================================================
with st.sidebar:
    st.title("🎬 Studio Control")
    st.markdown("---")
    
    target_tab = st.text_input("Target Sheet Tab", value="script", help="输入 Google Sheet 底部的 Tab 名称")
    
    if st.button("🚀 Start Rendering", type="primary", use_container_width=True):
        ok, msg = trigger_github_workflow(target_tab)
        if ok:
            st.cache_data.clear()  # 清理旧视频缓存
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
        run_id = run_info.get("id")
        
        if status in ["in_progress", "queued"]:
            st.info(f"⏳ **云端渲染中...** ({status})")
            st.progress(50)
            st.caption("您可以关闭此页面，渲染完成后重新打开即可预览视频。")
            if st.button("🔄 刷新进度", use_container_width=True):
                st.rerun()
        elif status == "completed":
            if conclusion == "success":
                st.success("🎉 **视频渲染成功！**")
                
                # 🌟 自动下载 mp4 视频并直接在 Streamlit 侧边栏预览播放
                with st.spinner("📥 正在加载视频预览窗口..."):
                    video_bytes = fetch_rendered_video_bytes(run_id)
                    
                if video_bytes:
                    st.video(video_bytes)
                    st.download_button(
                        label="⬇️ 一键下载视频成品",
                        data=video_bytes,
                        file_name="final_output.mp4",
                        mime="video/mp4",
                        type="primary",
                        use_container_width=True
                    )
                else:
                    st.warning("⚠️ 视频文件提取中，请稍等片刻后刷新。")
                    if st.button("🔄 重新拉取视频", use_container_width=True):
                        st.cache_data.clear()
                        st.rerun()
            else:
                st.error(f"❌ 渲染失败，结论: {conclusion}")
                st.markdown(f"[🔍 点击查看 GitHub 错误日志]({run_info.get('html_url')})")
    else:
        st.caption("暂无云端运行记录。")

# ==============================================================================
# 📊 主界面：最大化 Google 表格编辑视窗
# ==============================================================================
col_title, col_btn = st.columns([3, 1], vertical_alignment="center")

with col_title:
    st.subheader("📋 Google Sheets 内容在线编辑")

with col_btn:
    st.link_button("🔗 在新标签页打开表格", SHEET_EDIT_URL, use_container_width=True)

st.divider()

# 高清内嵌表格视窗
st.components.v1.iframe(SHEET_EMBED_URL, height=720, scrolling=True)
