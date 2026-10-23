import streamlit as st
import requests
import time
import zipfile
import io
import os
import streamlit.components.v1 as components

# ==========================================
# ⚠️ 页面基本配置 (必须放在代码的第一行)
# ==========================================
st.set_page_config(page_title="自动化视频引擎", page_icon="🎬", layout="wide")

# ==========================================
# ⚙️ 核心配置区
# ==========================================
GITHUB_REPO = "lishylishylishy/auto-video-maker"  
WORKFLOW_FILE = "render.yml"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit#gid=0" 

# 🔒 安全读取 GITHUB_TOKEN
try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    st.error("❌ 系统错误：未能在 Streamlit 后台找到 GITHUB_TOKEN，请在 App Settings -> Secrets 中配置。")
    st.stop()

# ==========================================
# 🎨 网页顶部标题
# ==========================================
st.title("🎬 自动化视频合成引擎")
st.markdown("---")

# ==========================================
# 🗂️ 核心功能区：使用选项卡 (Tabs) 分离功能，提升美观度
# ==========================================
tab1, tab2 = st.tabs(["🚀 视频渲染工作台", "📝 剧本与素材管理 (全屏编辑)"])

# ------------------------------------------
# Tab 1: 渲染工作台
# ------------------------------------------
with tab1:
    st.info("📌 **系统状态**：已绑定后台渲染引擎。请先在【剧本与素材管理】中确认配置无误后，再点击下方按钮。")
    
    # 用列布局让按钮居中显示，更好看
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        start_button = st.button("🚀 一键开始制作视频", use_container_width=True, type="primary")

    if start_button:
        # 1. 触发 GitHub Actions
        dispatch_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_TOKEN}"
        }
        payload = {"ref": "main"}
        
        with st.spinner("🚀 正在唤醒云端服务器，准备渲染..."):
            resp = requests.post(dispatch_url, headers=headers, json=payload)
            
        if resp.status_code != 204:
            st.error(f"❌ 触发失败，请检查 GITHUB_TOKEN 或仓库名。错误码: {resp.status_code}")
        else:
            time.sleep(5)
            runs_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs"
            
            with st.spinner("⏳ 视频正在云端火热合成中，大概需要 1-3 分钟，请不要刷新或关闭网页..."):
                runs_resp = requests.get(runs_url, headers=headers).json()
                if not runs_resp.get("workflow_runs"):
                    st.error("未能获取到任务状态，请稍后再试。")
                    st.stop()
                    
                run_id = runs_resp["workflow_runs"][0]["id"]
                
                # 轮询状态
                while True:
                    status_resp = requests.get(f"{runs_url}/{run_id}", headers=headers).json()
                    status = status_resp.get("status")
                    conclusion = status_resp.get("conclusion")
                    
                    if status == "completed":
                        if conclusion == "success":
                            break
                        else:
                            st.error("❌ 渲染过程中发生错误（如素材链接失效或表格读取失败），请检查 GitHub 后台日志。")
                            st.stop()
                    time.sleep(15)
            
            # 任务完成，下载视频
            with st.spinner("📥 渲染完成！正在把高清视频拉取到本网页..."):
                artifacts_url = f"{runs_url}/{run_id}/artifacts"
                arts_resp = requests.get(artifacts_url, headers=headers).json()
                
                if arts_resp.get("total_count", 0) > 0:
                    download_url = arts_resp["artifacts"][0]["archive_download_url"]
                    zip_resp = requests.get(download_url, headers=headers)
                    
                    try:
                        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
                            video_bytes = z.read("final_output.mp4")
                            
                            st.success("🎉 视频制作成功！快来看看吧 👇")
                            st.balloons()
                            
                            # 预览视频
                            st.video(video_bytes)
                            
                            # 下载按钮
                            st.download_button(
                                label="⬇️ 点击下载最终成片 (.mp4)",
                                data=video_bytes,
                                file_name="我的自动化成品视频.mp4",
                                mime="video/mp4",
                                use_container_width=True,
                                type="primary"
                            )
                    except Exception as e:
                        st.error(f"解析视频包失败: {e}")
                else:
                    st.error("❌ 云端任务完成了，但没找到输出的视频文件，请检查代码日志。")


# ------------------------------------------
# Tab 2: 剧本与表格内嵌编辑
# ------------------------------------------
with tab2:
    st.markdown("#### 📝 剧本与素材实时编辑")
    
    col_text, col_btn = st.columns([4, 1])
    with col_text:
        st.info("💡 提示：你可以直接在下方的大窗口中双击单元格修改台词和素材链接，Google 会自动实时保存。")
    with col_btn:
        st.link_button("🔗 新标签页全屏打开表格", SHEET_URL, use_container_width=True)

    # 配合宽屏模式，高度拉到800，视觉体验极大提升
    components.iframe(SHEET_URL, height=800, scrolling=True)
