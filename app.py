import streamlit as st
import requests
import time
import zipfile
import io
import os
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# 📝 剧本与配置编辑区
# ==========================================
st.markdown("### 📝 剧本与素材配置")

# 请将这里的链接替换为你真实的 Google Sheet 完整链接
# 注意：链接最好包含 /edit?gid=xxx，这样点击能直接定位到特定的 Tab（比如 Script 页）
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit#gid=Script"

col1, col2 = st.columns([2, 1])
with col1:
    st.info("💡 建议直接在 Google Sheet 原生界面编辑，排版不易错乱。")
with col2:
    # 增加一个醒目的跳转按钮
    st.link_button("👉 点击跳转到 Google Sheet 编辑", SHEET_URL, use_container_width=True)

# 提供一个折叠面板，允许直接在当前 Streamlit 网页内嵌显示和编辑
with st.expander("👀 或在当前网页内嵌查看/编辑表格", expanded=False):
    # 使用 iframe 嵌入 Google Sheet。只要你的浏览器登录了 Google 账号且有权限，就可以直接在这里打字编辑
    components.iframe(SHEET_URL, height=600, scrolling=True)
    
st.divider() # 加一条分割线，区分功能区
# ==========================================
# ⚙️ 网页控制台配置区
# ==========================================
# 填入你的 GitHub 信息
GITHUB_REPO = "lishylishylishy/auto-video-maker"  # 例如: "zhangsan/auto-video"
WORKFLOW_FILE = "render.yml"
# 🔒 安全读取GITHUB_TOKEN：从 Streamlit 的云端保险箱读取 Token，代码里干干净净
try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    st.error("❌ 系统错误：未能在 Streamlit 后台找到 GITHUB_TOKEN，请在 App Settings -> Secrets 中配置。")
    st.stop()

st.set_page_config(page_title="自动化视频引擎", page_icon="🎬")
st.title("🎬 自动化视频合成引擎")
st.markdown("---")
st.info("📌 **系统状态**：已绑定后台渲染引擎。点击下方按钮即可开始制作。")

if st.button("🚀 一键开始制作视频", use_container_width=True):
    # 1. 触发 GitHub Actions
    dispatch_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {GITHUB_TOKEN}"
    }
    payload = {"ref": "main"} # 触发主分支
    
    with st.spinner("🚀 正在唤醒云端服务器，准备渲染..."):
        resp = requests.post(dispatch_url, headers=headers, json=payload)
        
    if resp.status_code != 204:
        st.error(f"❌ 触发失败，请检查 GITHUB_TOKEN 或仓库名。错误码: {resp.status_code}")
    else:
        # 2. 等待几秒钟让 GitHub 生成任务 ID
        time.sleep(5)
        runs_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs"
        
        with st.spinner("⏳ 视频正在云端火热合成中，大概需要几分钟，请不要关闭网页..."):
            # 获取刚刚触发的运行任务
            runs_resp = requests.get(runs_url, headers=headers).json()
            if not runs_resp.get("workflow_runs"):
                st.error("未能获取到任务状态，请稍后再试。")
                st.stop()
                
            run_id = runs_resp["workflow_runs"][0]["id"]
            
            # 3. 轮询等待任务完成 (每 15 秒问一次 GitHub：做完了吗？)
            while True:
                status_resp = requests.get(f"{runs_url}/{run_id}", headers=headers).json()
                status = status_resp.get("status")
                conclusion = status_resp.get("conclusion")
                
                if status == "completed":
                    if conclusion == "success":
                        break
                    else:
                        st.error("❌ 渲染过程中发生错误（如素材链接失效或表格读取失败），请检查后台日志。")
                        st.stop()
                time.sleep(15)
        
        # 4. 任务完成，自动下载视频并展示在网页上
        with st.spinner("📥 渲染完成！正在把视频拉取到本网页..."):
            artifacts_url = f"{runs_url}/{run_id}/artifacts"
            arts_resp = requests.get(artifacts_url, headers=headers).json()
            
            if arts_resp.get("total_count", 0) > 0:
                download_url = arts_resp["artifacts"][0]["archive_download_url"]
                # 下载产物 ZIP
                zip_resp = requests.get(download_url, headers=headers)
                
                try:
                    # 在内存中解压 ZIP 拿到 mp4
                    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
                        # 你的 main.py 导出的名字叫 final_output.mp4
                        video_bytes = z.read("final_output.mp4")
                        
                        st.success("🎉 视频制作成功！")
                        st.balloons()
                        
                        # 直接在网页预览视频
                        st.video(video_bytes)
                        
                        # 提供直接下载按钮
                        st.download_button(
                            label="⬇️ 下载最终视频",
                            data=video_bytes,
                            file_name="我的自动化成品视频.mp4",
                            mime="video/mp4",
                            use_container_width=True
                        )
                except Exception as e:
                    st.error(f"解析视频包失败: {e}")
            else:
                st.error("❌ 云端任务完成了，但没找到输出的视频文件。")
