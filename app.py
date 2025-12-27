import streamlit as st

# 1. 页面配置 [cite: 144, 145]
st.set_page_config(page_title="科研绘图助手 Demo", layout="wide")

# 2. 侧边栏：显示当前状态 [cite: 264]
with st.sidebar:
    st.title("项目进度")
    st.success("📍 当前环节: 初始输入")

st.title("科研绘图助手 (DrawAgent) ")
st.caption("基于 Agentic Workflow 的可编辑绘图工具 ")

# 3. 左右分栏布局：四六开 [cite: 144]
col_input, col_output = st.columns([4, 6])

with col_input:
    st.subheader("对话与逻辑控制区 ")

    # 统一输入框：支持长文本 [cite: 82, 146]
    user_text = st.text_area(
        label="在此输入内容",
        placeholder="粘贴论文摘要、实验步骤，或上传手绘草图... ",
        height=300
    )

    # 图片上传组件 [cite: 85, 138, 148]
    uploaded_file = st.file_uploader("点此上传附件", type=["jpg", "png"])

    if st.button("提交", use_container_width=True): # [cite: 139, 149]
        # 4. 路由层逻辑 (Router Logic) [cite: 87, 88, 89]
        if uploaded_file:
            st.info("检测到附件：进入【4.3 草图还原流】 ")
        elif len(user_text) > 200:
            st.info("检测到长文本：进入【4.2 文本转图流】 ")
        else:
            st.info("进入【4.4 创意共创流】 ")

with col_output:
    st.subheader("输出与演示区 ")
    st.write("无输出 ")