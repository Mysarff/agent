import asyncio
import os
import hmac

import streamlit as st

from TripWeave.intelligence.demo import EXAMPLES
from TripWeave.intelligence.runtime import build_engine


def clear_current():
    st.session_state.engine.reset()
    st.session_state.messages = []
    st.session_state.last_run = None


def confirm_pending():
    run = st.session_state.last_run
    confirmed = asyncio.run(st.session_state.engine.confirm(run.pending_token))
    run.pending_token = None
    run.results = [confirmed if r.step_id == confirmed.step_id else r for r in run.results]
    st.session_state.messages.append({"role": "assistant", "content": confirmed.text})


def cancel_pending():
    run = st.session_state.last_run
    st.session_state.engine.cancel(run.pending_token)
    run.pending_token = None
    for item in run.results:
        if item.status == "awaiting_confirmation":
            item.status = "cancelled"
            item.text = "已取消模拟操作。"
    st.session_state.messages.append({"role": "assistant", "content": "已取消模拟操作。"})


st.set_page_config(page_title="行知 TripWeave · 多 Agent 旅行助手", page_icon="🧭", layout="wide")
access_password = os.getenv('TRIPWEAVE_ACCESS_PASSWORD')
if access_password and not st.session_state.get('access_granted'):
    st.title('行知 TripWeave · 私有演示')
    with st.form('access'):
        supplied = st.text_input('访问口令', type='password')
        if st.form_submit_button('进入'):
            if hmac.compare_digest(supplied.encode(), access_password.encode()):
                st.session_state.access_granted = True
                st.rerun()
            else:
                st.error('口令不正确')
    st.stop()
st.title("🧭 行知 TripWeave · 多 Agent 旅行助手")
st.caption("连续查询天气和车票，选择候选、补齐数量，确认后创建模拟订单。")
choices = ["离线演示", "真实协议演示（规则模型）", "LLM 多 Agent 协作"]
default_mode = 2 if os.getenv('TRIPWEAVE_MODEL_MODE') == 'llm' else 1 if os.getenv('TRIPWEAVE_STACK') == '1' else 0
requested_mode = st.sidebar.selectbox("运行方式", choices, index=default_mode, key='requested_mode')
mode = requested_mode
model_configured = bool(os.getenv('TRIPWEAVE_API_KEY') and os.getenv('TRIPWEAVE_MODEL'))
llm_ready = model_configured and os.getenv('TRIPWEAVE_MODEL_MODE') == 'llm'
if requested_mode == choices[2] and not llm_ready:
    mode = choices[1] if os.getenv('TRIPWEAVE_STACK') == '1' else choices[0]
    st.warning('大模型尚未就绪，下面暂用示例模式运行，未调用 LLM。你可以先点击示例体验完整流程。')
    with st.expander('如何启用大模型自由问答', expanded=True):
        st.write('在项目根目录把 .env.example 复制为 .env，填写自己的模型名称与密钥，然后重启服务。不要把密钥发到聊天或上传 GitHub。')
        st.code('TRIPWEAVE_API_KEY=填写你自己的密钥\nTRIPWEAVE_MODEL=填写服务商的模型名称\nTRIPWEAVE_BASE_URL=填写服务商的兼容接口地址', language='text')
        st.code('python -m TripWeave.services.stack --live', language='bash')
        st.caption('BASE_URL 使用默认服务地址时可留空。重启后，协调器和领域 Agent 才会一起使用大模型。')
    st.sidebar.caption('实际运行：' + mode)
if st.session_state.get("engine_mode") != mode:
    try:
        st.session_state.engine = build_engine(demo=mode in (choices[0], choices[1]), network=mode in (choices[1], choices[2]))
    except ValueError as exc:
        st.warning('模型初始化未完成，已切换到离线示例。请检查配置后重启。')
        mode = choices[0]
        st.session_state.engine = build_engine(demo=True)
    st.session_state.engine_mode = mode
    st.session_state.messages = []
    st.session_state.last_run = None

engine = st.session_state.engine
if mode == "离线演示":
    st.info("离线规则演示：使用会话临时样例库，不调用 A2A/MCP 网络或付费模型。关闭会话后离线订单不保留。可逐句输入以下示例。")
    for example in EXAMPLES:
        st.code(example, language=None)
elif mode in (choices[1], choices[2]):
    st.info('当前主链路经过独立领域 Agent 和真实 MCP 服务。票务为演示库，订单只在本地模拟；规则模式不代表 LLM 理解效果。')
    st.code('北京2026-10-01的天气 → 查同一天去上海的火车票 → 订第二个 → 1张', language=None)

example_query = None
if not st.session_state.messages:
    st.subheader('从一个问题开始')
    st.write('点击下面的示例，或在底部输入问题。涉及模拟预订时，会先展示待确认任务。')
    samples = [
        ('开始多轮对话', '先查天气，再逐句查票和选择。', EXAMPLES[0]),
        ('查看候选车票', '查看带序号的两条演示车票。', '查询北京到上海2026-10-01的火车票'),
        ('体验多 Agent 协作', '并行查天气和票，选择第二个并生成确认单。',
         '查询北京2026-10-01的天气和北京到上海2026-10-01的火车票，预订第二个，1张')]
    for column, (label, description, question) in zip(st.columns(3), samples):
        with column:
            st.write(description)
            if st.button(label, use_container_width=True):
                example_query = question

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

query = st.chat_input("输入旅行问题，或先点击上方示例") or example_query
if query:
    with st.chat_message("user"):
        st.markdown(query)
    with st.spinner("正在选择服务并处理请求…"):
        result = asyncio.run(engine.run(query, st.session_state.messages))
    st.session_state.messages.extend([{"role": "user", "content": query},
                                      {"role": "assistant", "content": result.render()}])
    st.session_state.last_run = result
    with st.chat_message("assistant"):
        st.markdown(result.render())

run = st.session_state.last_run
if run:
    if run.pending_token:
        st.warning("请检查待确认的模拟操作。确认不会产生真实出票。")
        left, right = st.columns(2)
        left.button("确认模拟操作", on_click=confirm_pending)
        right.button("取消", on_click=cancel_pending)
    with st.expander("查看任务与执行记录"):
        st.json({"run_id": run.run_id, "routing": run.routing_trace,
                 "plan": run.plan.model_dump() if run.plan else None,
                 "steps": [{"step": r.step_id, "capability": r.capability, "status": r.status,
                            "elapsed_ms": r.elapsed_ms, "error_code": r.error_code, "protocol_trace": r.trace} for r in run.results]})
    with st.expander('本轮使用的出行条件'):
        st.json(engine.session.snapshot())

with st.sidebar.expander("可用能力（注册配置，不代表服务在线）"):
    for capability in engine.registry.items.values():
        st.write(f"**{capability.id}**：{capability.description}")
st.sidebar.button("清空当前会话", on_click=clear_current)
st.sidebar.caption("基于课程旅行助手二次开发；改动对照见 README。")
