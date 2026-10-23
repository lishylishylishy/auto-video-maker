import streamlit as st
import requests
import json
import os

# ==========================================
# ⚙️ 固定配置区
# ==========================================
# 把你的私密 Google Sheet 链接直接写死在这里
FIXED_SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit?gid=0#gid=0" 

# GitHub 仓库信息 (用于触发 Action)
GITHUB_REPO = "auto-video-maker" # 例如 "lishy/auto-video-maker"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "如果本地运行请填入你的Token")
WORKFLOW_FILE = "render.yml" # 你的 GitHub Actions 工作流文件名

# ==========================================
# 🎨 前端界面展示
# ==========================================
st.set_page_config(page_title="自动化视频引擎", page_icon="🎬")
st.title("🎬 自动化视频合成引擎")
st.markdown("---")

st.info("📌 **系统状态**：已成功绑定私密剧本与配置表格。")
st.write("在表格中更新完剧本 (`script`) 和配置 (`config`) 后，直接点击下方按钮即可触发云端渲染。")

# ==========================================
# 🚀 触发后台渲染逻辑
# ==========================================
if st.button("🚀 一键开始制作视频", use_container_width=True):
    with st.spinner("正在呼叫云端服务器启动渲染..."):
        
        # 调用 GitHub API 触发 Actions
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {GITHUB_TOKEN}"
        }
        # 将固定好的表格链接打包发给 GitHub
        payload = {
            "ref": "main",
            "inputs": {
                "sheet_url": FIXED_SHEET_URL
            }
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 204:
            st.success("✅ 渲染任务已成功提交至云端！请稍后前往 GitHub Actions 查看进度或等待成品输出。")
        else:
            st.error(f"❌ 任务提交失败！错误代码：{response.status_code}")
            st.write(response.text)
